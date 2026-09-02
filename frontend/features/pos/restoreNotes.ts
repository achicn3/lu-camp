// 還原購物車時重新取回商品備註。
//
// 為什麼需要這一步：`staff_payload` 是**銷售請求**的形狀（沒有 note），客顯快照更只有
// 品名與金額。少了它，重整／崩潰復原／換店員接手之後，所有商品都會被當成沒有備註 →
// 結帳不再提醒，等於這個功能在最需要它的場合（換人接手）失效。
// 順帶取到的是**最新**備註：掃碼之後有人改過也會反映。
import type { CartLine } from "@/features/pos/cart";
import { api } from "@/lib/api";

/** 取單行的最新備註；取不到（404／權限／網路）一律回 undefined，不讓還原本身失敗。 */
async function fetchNote(line: CartLine): Promise<string | null | undefined> {
  try {
    if (line.lineType === "SERIALIZED" && line.itemCode) {
      const { data } = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
        params: { path: { item_code: line.itemCode } },
      });
      return data?.note;
    }
    if (line.lineType === "CATALOG" && line.catalogProductId != null) {
      const { data } = await api.GET("/api/v1/catalog-products/{product_id}", {
        params: { path: { product_id: line.catalogProductId } },
      });
      return data?.note;
    }
    if (line.lineType === "BULK_LOT" && line.bulkLotId != null) {
      const { data } = await api.GET("/api/v1/bulk-lots/{lot_id}", {
        params: { path: { lot_id: line.bulkLotId } },
      });
      return data?.note;
    }
  } catch {
    // 還原不能因為補備註失敗而中斷；沒取到就維持沒有備註。
  }
  return undefined;
}

/** 把還原出來的購物車行補上最新備註（餐飲行沒有庫存備註，原樣返回）。 */
export async function withFreshNotes(restored: CartLine[]): Promise<CartLine[]> {
  return await Promise.all(
    restored.map(async (line): Promise<CartLine> => {
      const note = await fetchNote(line);
      return note === undefined ? line : { ...line, note };
    }),
  );
}
