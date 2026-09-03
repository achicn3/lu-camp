// 還原購物車時重新取回商品備註。
//
// 為什麼需要這一步：`staff_payload` 是**銷售請求**的形狀（沒有 note），客顯快照更只有
// 品名與金額。少了它，重整／崩潰復原／換店員接手之後，所有商品都會被當成沒有備註 →
// 結帳不再提醒，等於這個功能在最需要它的場合（換人接手）失效。
// 順帶取到的是**最新**備註：掃碼之後有人改過也會反映。
//
// **取不到 ≠ 沒有備註**（fail closed）：短暫 401／500／斷線若被當成「這件沒有備註」，
// 「先別賣」就會無聲消失——正是本修正要避免的事。取不到的行標記 `noteUnknown`，
// 結帳提醒會把它列出來要求店員自行查證，而不是靜默放行。
// 只有 404（商品不存在／他店）才算「確定沒有備註」。
import type { CartLine } from "@/features/pos/cart";
import { api } from "@/lib/api";

/** 單行的備註查詢結果：拿到內容、確定沒有、或**沒問到**。 */
type NoteLookup =
  | { kind: "resolved"; note: string | null }
  | { kind: "absent" }
  | { kind: "unknown" };

async function fetchNote(line: CartLine): Promise<NoteLookup> {
  try {
    if (line.lineType === "SERIALIZED" && line.itemCode) {
      const { data, response } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
        params: { path: { item_code: line.itemCode } },
      });
      return classify(data?.note, response.status);
    }
    if (line.lineType === "CATALOG" && line.catalogProductId != null) {
      const { data, response } = await api.GET("/api/v1/catalog-products/{product_id}", {
        params: { path: { product_id: line.catalogProductId } },
      });
      return classify(data?.note, response.status);
    }
    if (line.lineType === "BULK_LOT" && line.bulkLotId != null) {
      const { data, response } = await api.GET("/api/v1/bulk-lots/{lot_id}", {
        params: { path: { lot_id: line.bulkLotId } },
      });
      return classify(data?.note, response.status);
    }
  } catch {
    // 網路層失敗（斷線、CORS、逾時）：問不到，不能當成沒有備註。
    return { kind: "unknown" };
  }
  // 餐飲行等沒有庫存備註可查的型態。
  return { kind: "absent" };
}

function classify(note: string | null | undefined, status: number): NoteLookup {
  if (status === 200) return { kind: "resolved", note: note ?? null };
  // 商品查無（已刪／他店）：確定沒有備註可顯示，不必要求店員查證。
  if (status === 404) return { kind: "absent" };
  return { kind: "unknown" };
}

/**
 * 補抓備註的逐行逾時。**收銀台永遠不能因為補備註而結不了帳**：伺服器收了連線卻不
 * 回應時（沒有錯誤、也不 settle），沒有逾時就會讓還原一直卡著、結帳鍵永久停用——
 * 那比漏提醒嚴重得多。逾時的行當成「沒問到」，照樣在提醒裡列出請店員自行查證。
 * 後端就在同一台機器上，正常回應是毫秒級；3 秒已經非常寬鬆。
 */
export const NOTE_FETCH_TIMEOUT_MS = 3000;

/** 逐行競速：先到者為準；逾時就回 fallback，不等原本那個 promise。 */
function withTimeout(
  pending: Promise<NoteLookup>,
  timeoutMs: number,
  fallback: NoteLookup,
): Promise<NoteLookup> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), timeoutMs);
    void pending.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

/**
 * 把還原出來的購物車行補上最新備註。
 * 查得到 → 帶入 note；問不到／逾時 → `noteUnknown: true`；確定沒有 → 原樣。
 * 逾時逐行獨立：一件卡住不拖累其他件。
 */
export async function withFreshNotes(
  restored: CartLine[],
  options: { timeoutMs?: number } = {},
): Promise<CartLine[]> {
  const timeoutMs = options.timeoutMs ?? NOTE_FETCH_TIMEOUT_MS;
  return await Promise.all(
    restored.map(async (line): Promise<CartLine> => {
      const lookup = await withTimeout(fetchNote(line), timeoutMs, { kind: "unknown" });
      if (lookup.kind === "resolved") return { ...line, note: lookup.note };
      if (lookup.kind === "unknown") return { ...line, noteUnknown: true };
      return line;
    }),
  );
}
