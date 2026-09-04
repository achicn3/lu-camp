"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import type { CartLine } from "@/features/pos/cart";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

/**
 * 還原待決的硬性期限。這段期間掃碼是停用的，所以不能太長；但開店第一次載入
 * （backend 剛起、頁面剛編譯、機器剛開機）本來就會慢，太短又等於沒鎖。
 * 5 秒是折衷：正常狀況零點幾秒就放行，只有伺服器不回應時才真的等滿。
 */
export const RESTORE_GUARD_TIMEOUT_MS = 5000;

type SaleLine = components["schemas"]["SaleLineCreateRequest"];
type Tender = components["schemas"]["CartTenderRequest"];
type Adjustment = components["schemas"]["SaleAdjustmentRequest"];
type StaffCart = components["schemas"]["StaffCartSessionRead"];
type CartSession = components["schemas"]["CartSessionRead"];
type CartItem = components["schemas"]["CartItemRead"];
type Terminal = components["schemas"]["TerminalRead"];

const TERMINAL_INSTALLATION_KEY = "lu-camp.pos-terminal.installation";

export function terminalInstallationId(): string {
  const existing = window.localStorage.getItem(TERMINAL_INSTALLATION_KEY);
  if (existing) return existing;
  const generated = newIdempotencyKey();
  window.localStorage.setItem(TERMINAL_INSTALLATION_KEY, generated);
  return generated;
}

function itemIdentity(item: CartItem): Partial<CartLine> | null {
  // 贈品的 item_key 帶 GIFT: 前綴（GIFT:CATALOG:12）。以第一個冒號切會得到 "CATALOG:12"，
  // Number() 解析失敗就把整行丟掉——重整後贈品直接消失。先剝掉前綴再切。
  const withoutKind = item.item_key.startsWith("GIFT:")
    ? item.item_key.slice("GIFT:".length)
    : item.item_key;
  const separator = withoutKind.indexOf(":");
  if (separator < 1) return null;
  const raw = withoutKind.slice(separator + 1);
  const giftPrefix = item.line_kind === "GIFT" ? "G:" : "";
  switch (item.line_type) {
    case "SERIALIZED":
      return { key: `${giftPrefix}S:${raw}`, itemCode: raw, maxQty: 1 };
    case "CATALOG": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `${giftPrefix}C:${id}`, catalogProductId: id }
        : null;
    }
    case "BULK_LOT": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `${giftPrefix}B:${id}`, bulkLotId: id }
        : null;
    }
    case "MENU": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `${giftPrefix}M:${id}`, menuItemId: id }
        : null;
    }
  }
}

/** POS 重整時只以後端快照重建顯示；價格仍會在下一次 quote／sync 由後端重算。 */
export function restoreLines(items: CartItem[]): CartLine[] {
  return items.flatMap((item) => {
    const identity = itemIdentity(item);
    if (!identity?.key) return [];
    return [
      {
        ...identity,
        key: identity.key,
        lineType: item.line_type,
        description: item.name,
        unitPrice: parseNtd(item.unit_price) ?? 0,
        qty: item.qty,
        lineKind: item.line_kind,
      },
    ];
  });
}

interface PosCustomerDisplayProps {
  lines: SaleLine[];
  buyerContactId: number | null;
  tenders: Tender[];
  /** 臨時折扣：客顯是權威購物車，折扣不經它，客人螢幕上的金額就會與實際扣款不同。 */
  adjustments: Adjustment[];
  /** 餐飲內用/外帶與桌號（docs/35）：跟著購物車保存，POS 重掛/還原時才不會遺失選擇。 */
  serviceMode: "DINE_IN" | "TAKEOUT" | null;
  tableNo: string | null;
  ready: boolean;
  onRestore: (cart: StaffCart) => void | Promise<void>;
  onTerminalChange?: (terminal: Terminal | null) => void;
  onCartChange?: (cart: CartSession | StaffCart | null) => void;
  /** 畫面上的購物車內容是否還有變更沒同步到伺服器（送簽前必須是 false）。 */
  onSyncDirtyChange?: (dirty: boolean) => void;
  /**
   * 「還原是否還沒定案」：從掛載到「確定沒有東西要還原」或「還原完成」為止為 true。
   * POS 頁據此鎖住購物車操作——這段期間掃進去的商品會**雙重失效**：同步 effect 被
   * hydrated 擋著推不上伺服器，接著 onRestore 的 setLines 又整批覆蓋掉，商品無聲消失。
   */
  onRestorePendingChange?: (pending: boolean) => void;
}

type PendingSync = { fingerprint: string } & (
  | {
      kind: "UPSERT";
      lines: SaleLine[];
      buyerContactId: number | null;
      tenders: Tender[];
      adjustments: Adjustment[];
      serviceMode: "DINE_IN" | "TAKEOUT" | null;
      tableNo: string | null;
    }
  | { kind: "CANCEL" }
);

export function PosCustomerDisplay({
  lines,
  buyerContactId,
  tenders,
  adjustments,
  serviceMode,
  tableNo,
  ready,
  onRestore,
  onTerminalChange,
  onCartChange,
  onSyncDirtyChange,
  onRestorePendingChange,
}: PosCustomerDisplayProps) {
  const queryClient = useQueryClient();
  const [pairingCode, setPairingCode] = useState("");
  const [syncError, setSyncError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [syncedRevision, setSyncedRevision] = useState<number | null>(null);
  // 最後一次**成功**推上去的內容指紋。與目前畫面的指紋不同＝還有變更在防抖或在途。
  // 只比對餐飲欄位是不夠的：移除最後一筆餐飲時，本地立刻沒有餐飲了，餐飲比對隨即
  // 「一致」，但伺服器上還是舊的餐飲購物車（Codex 第三輪）。整車指紋才擋得住，
  // 也順帶解決 A1→A2→A1 這種改回原值的情況。
  const [syncedFingerprint, setSyncedFingerprint] = useState<string | null>(null);
  // 有請求排隊或在途嗎。**只比指紋不夠**：A1→A2（PUT 已送出、回應還沒處理）→改回 A1，
  // 現值又等於「最後成功」的 A1，dirty 會誤判成 false，而伺服器上其實是 A2
  //（Codex 第四輪）。在途期間一律視為未同步。
  const [syncBusy, setSyncBusy] = useState(false);
  const payloadFingerprint = JSON.stringify({
    lines,
    buyerContactId,
    tenders,
    adjustments,
    serviceMode,
    tableNo,
  });
  const payload = useRef({
    lines,
    buyerContactId,
    tenders,
    adjustments,
    serviceMode,
    tableNo,
  });
  // drain 是 useCallback，內部拿不到最新的 payloadFingerprint；用 ref 帶進去。
  // **在 effect 裡寫入**，不在 render 期間碰 ref（React 規則）。
  const payloadFingerprintRef = useRef(payloadFingerprint);
  const revision = useRef<number | null>(null);
  const pending = useRef<PendingSync | null>(null);
  const draining = useRef(false);
  const hydratedTerminal = useRef<number | null>(null);
  const terminal = useQuery({
    queryKey: ["customer-display", "terminal"],
    retry: false,
    refetchInterval: 15_000,
    queryFn: async () => {
      const { data, error } = await api.POST("/api/v1/customer-display/terminals", {
        body: {
          installation_id: terminalInstallationId(),
          name: "主要櫃檯",
        },
      });
      if (!data) {
        const detail =
          error && typeof error === "object" && "detail" in error
            ? String(error.detail)
            : "無法註冊 POS 櫃檯";
        throw new Error(detail);
      }
      return data;
    },
  });
  const current = useQuery({
    queryKey: ["customer-display", "cart", terminal.data?.id],
    enabled: terminal.data?.paired_kiosk != null,
    retry: false,
    queryFn: async () => {
      if (!terminal.data) return null;
      const { data, response } = await api.GET(
        "/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        { params: { path: { terminal_id: terminal.data.id } } },
      );
      if (!response.ok) throw new Error("無法恢復顧客螢幕購物車");
      return data ?? null;
    },
  });

  // 還原待決：從掛載算起，直到**確定不會有還原**或**還原已完成**為止。
  //
  // 刻意由「已知的資料」推導，不看 React Query 的 isPending/isLoading：
  //   - v5 的 disabled 查詢 isPending 也是 true（沒配對顧客螢幕的店會被永久鎖死）
  //   - 用 isLoading 則在「terminal 剛好、current 還沒開始抓」之間有一個 render 的破口
  // 每一條分支都是「已經知道答案」才放行，順序即優先序。
  const restoreSettled =
    terminal.isError ||
    (terminal.data != null &&
      (terminal.data.paired_kiosk == null || // 沒配對 → 不會有還原
        current.isError || // 問不到 → 放行，別擋著做生意
        (current.isSuccess && (current.data == null || hydrated))));

  // 硬性期限：伺服器收了連線卻不回應時，上面每個條件都會永遠停在「還不知道」。
  // **鎖死收銀台比原本的漏單嚴重得多**，時間到就退回原本的風險，讓店員做得了生意。
  //
  // 期限跟著「每一次待決」重新起算，不是只在掛載時算一次：顧客螢幕**事後才配對**時
  // restoreSettled 會從已定案退回待決（開始問 cart/current），若沿用掛載時那個早就
  // 用掉的期限，那次還原就完全沒有保護，等於窗口又開回來。
  // 每一次「從已定案轉為待決」就是一次新的還原嘗試，用遞增序號識別。
  //
  // 先前用「終端 × 顧客螢幕」當鍵是錯的：那代表配對組合，不代表一次嘗試。同一組第一次
  // 逾時之後，斷線重連或重抓再度進入待決時會被當成「已經過期」，鎖立刻放開、遲到的
  // 舊車也照樣作廢——兩邊的保護都失效。序號則是每次待決都全新起算。
  const restoreAttemptRef = useRef(0);
  const settledRef = useRef(true);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  useEffect(() => {
    if (restoreSettled) {
      settledRef.current = true;
      return;
    }
    if (!settledRef.current) return; // 同一次待決中，不重複起算
    settledRef.current = false;
    restoreAttemptRef.current += 1;
    setRestoreAttempt(restoreAttemptRef.current);
  }, [restoreSettled]);

  const [expiredAttempt, setExpiredAttempt] = useState(0);
  useEffect(() => {
    if (restoreSettled || restoreAttempt === 0) return;
    const timer = window.setTimeout(
      () => setExpiredAttempt(restoreAttempt),
      RESTORE_GUARD_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [restoreAttempt, restoreSettled]);

  // restoreAttempt === 0 代表序號還沒建立（第一個 render，effect 尚未執行）。
  // 這時 expiredAttempt 也是 0，若不特別處理就會算成「已過期」→ 回報 false，
  // 把父層 fail-closed 的初值蓋掉，留下一個 commit 的破口。未定案就是待決。
  const restorePending =
    !restoreSettled && (restoreAttempt === 0 || expiredAttempt !== restoreAttempt);
  // 這一次嘗試是否已經超過期限（鎖已放開，店員可能已經動過購物車）。
  const restoreExpired = restoreAttempt !== 0 && expiredAttempt === restoreAttempt;
  useEffect(() => {
    onRestorePendingChange?.(restorePending);
  }, [onRestorePendingChange, restorePending]);

  useEffect(() => {
    onTerminalChange?.(terminal.data ?? null);
  }, [onTerminalChange, terminal.data]);

  useEffect(() => {
    if (current.isSuccess) onCartChange?.(current.data ?? null);
  }, [current.data, current.isSuccess, onCartChange]);

  useEffect(() => {
    payload.current = {
      lines,
      buyerContactId,
      tenders,
      adjustments,
      serviceMode,
      tableNo,
    };
  }, [adjustments, buyerContactId, lines, serviceMode, tableNo, tenders]);

  useEffect(() => {
    const terminalId = terminal.data?.id ?? null;
    if (terminalId !== hydratedTerminal.current) {
      hydratedTerminal.current = terminalId;
      revision.current = null;
      setSyncedRevision(null);
      setHydrated(false);
    }
  }, [terminal.data?.id]);

  useEffect(() => {
    if (!current.isSuccess || hydrated || terminal.data == null) return;
    // 重掛時 React Query 會**先同步吐出舊快取**再背景重抓（預設 staleTime 0）。
    // 完成一筆後 resetSale 會讓本元件重新掛載，若吃到上一筆的快取，剛賣掉的商品、
    // 簽署與付款狀態會整組回到下一筆交易。只認本次掛載之後才真的抓回來的資料。
    //
    // 用 isFetchedAfterMount 而非比對時間戳：時鐘比對在「掛載與取得資料落在同一毫秒」
    // 時會誤判成舊快取而該還原卻不還原，也怕時鐘回撥。這個旗標就是這個語意本身。
    if (!current.isFetchedAfterMount) return;
    revision.current = current.data?.revision ?? null;
    setSyncedRevision(revision.current);
    let active = true;
    // 逾時解鎖只是讓店員能做生意，**不代表遲到的還原可以覆蓋他做過的事**。
    // 期限過後店員可能已經掃了東西；此時才回來的舊購物車一律作廢，否則等於把窗口
    // 從「掛載到還原之間」挪到「第 5 秒之後」，根本沒關上。
    // 購物車還空著就照常還原——沒有東西會被蓋掉，也不必白白丟掉未結完的單。
    // 用 payload ref 而非 lines prop：ref 由專責 effect 維護、恆為最新的已提交內容，
    // 不必把 lines 塞進相依（每掃一件就重跑這條 effect），也不必抑制 lint 規則。
    // 逾時後才回來的還原：店員可能已經掃了東西，不能覆蓋他的操作。
    // **但只有 DRAFT 可以被本地取代**——FROZEN／PROCESSING／PAYMENT_UNCERTAIN 的購物車
    // 同步 effect 明文拒絕推送，作廢了本地也當不成權威：簽署任務、會員與付款方式都沒
    // 還原，畫面卻被 displayCart 鎖住，變成既推不上去也結不了帳。那三種狀態一律照常
    // 還原，讓店員拿回撤回／對帳的入口。
    const serverCartOverwritable = current.data?.status === "DRAFT";
    const discardStaleRestore =
      restoreExpired && serverCartOverwritable && payload.current.lines.length > 0;
    void Promise.resolve(
      current.data && !discardStaleRestore ? onRestore(current.data) : undefined,
    ).then(() => {
      // 作廢的情況也要 hydrate：讓同步恢復，由本地購物車成為權威推上去，
      // 不然畫面與顧客螢幕會一直對不起來。
      if (active) setHydrated(true);
    });
    return () => {
      active = false;
    };
  }, [
    current.data,
    current.isFetchedAfterMount,
    current.isSuccess,
    hydrated,
    onRestore,
    restoreExpired,
    terminal.data,
  ]);

  useEffect(() => {
    if (!hydrated || !current.isSuccess || current.data == null) return;
    // freeze / cancel / begin-checkout 由 POS 頁的其他 mutation 更新同一購物車。current refetch
    // 讀到較新 revision 時同步內部 CAS 基準；只接受單調遞增，舊回應不得倒灌覆蓋。
    if (
      revision.current === null ||
      current.data.revision > revision.current
    ) {
      revision.current = current.data.revision;
    }
  }, [current.data, current.isSuccess, hydrated]);

  const drain = useCallback(async (terminalRow: Terminal) => {
    if (draining.current) return;
    draining.current = true;
    let attempted: string | null = null;
    try {
      while (pending.current) {
        const next = pending.current;
        attempted = next.fingerprint;
        pending.current = null;
        if (next.kind === "UPSERT") {
          const { data, response } = await api.PUT(
            "/api/v1/customer-display/terminals/{terminal_id}/cart",
            {
              params: { path: { terminal_id: terminalRow.id } },
              body: {
                expected_revision: revision.current,
                lines: next.lines,
                buyer_contact_id: next.buyerContactId,
                tenders: next.tenders.length > 0 ? next.tenders : null,
                adjustments:
                  next.adjustments.length > 0 ? next.adjustments : null,
                service_mode: next.serviceMode,
                table_no: next.tableNo,
              },
            },
          );
          if (!data) {
            throw new Error(
              response.status === 409
                ? "顧客螢幕的購物車和這裡對不起來，請重新整理 POS 後再操作。"
                : "顧客螢幕同步失敗，請確認店內網路。",
            );
          }
          revision.current = data.revision;
          setSyncedRevision(data.revision);
          setSyncedFingerprint(next.fingerprint);
          onCartChange?.(data);
        } else if (revision.current !== null) {
          const { data } = await api.POST(
            "/api/v1/customer-display/terminals/{terminal_id}/cart/cancel",
            {
              params: { path: { terminal_id: terminalRow.id } },
              body: {
                expected_revision: revision.current,
                reason: "店員清空購物車",
              },
            },
          );
          if (!data) throw new Error("顧客螢幕沒有清空成功，請重新整理 POS。");
          revision.current = null;
          setSyncedRevision(null);
          setSyncedFingerprint(next.fingerprint);
          onCartChange?.(null);
        }
        setSyncError(null);
      }
    } catch (error) {
      pending.current = null;
      // **失敗的那份已經過時就不要卡住**：掃了 A（PUT 在途）又掃 B，A 才失敗時，
      // 若照樣設下阻擋性錯誤，B 從此送不出去也不會再有人重試——除非店員剛好又動一次
      // 購物車（Codex 第五輪）。連續掃商品時前一個請求還在途是常態，不是罕見時序。
      // 內容已經往前走了 → 不擋，直接以最新內容重試；只有「失敗的正是目前這份」
      // 才是真的該讓店員看到的故障。
      if (attempted !== null && attempted !== payloadFingerprintRef.current) {
        // 把**最新**內容重新排進去，交給 finally 的補送機制送出；
        // 只是 return 的話 pending 是空的，等於沒有人會再送 B。
        const latest = payload.current;
        pending.current =
          latest.lines.length > 0
            ? {
                kind: "UPSERT",
                fingerprint: payloadFingerprintRef.current,
                lines: latest.lines,
                buyerContactId: latest.buyerContactId,
                tenders: latest.tenders,
                adjustments: latest.adjustments,
                serviceMode: latest.serviceMode,
                tableNo: latest.tableNo,
              }
            : { kind: "CANCEL", fingerprint: payloadFingerprintRef.current };
        return;
      }
      setSyncError(error instanceof Error ? error.message : "顧客螢幕同步失敗");
    } finally {
      draining.current = false;
      // 迴圈判定「沒有待送」到這裡之間，排程可能剛放進新的一筆；而那次呼叫會因為
      // draining 仍為 true 而直接返回 → 沒有人來送它。窄，但我的 syncBusy 會把它
      // 變成送簽按鈕永久變灰，所以在這裡補送一次。
      if (pending.current !== null) {
        void drainRef.current?.(terminalRow);
      } else {
        setSyncBusy(false);
      }
    }
  }, [onCartChange]);

  // drain 需要在自己的 finally 裡再呼叫自己；用 ref 轉一手避免 useCallback 自我相依。
  const drainRef = useRef<((terminalRow: Terminal) => Promise<void>) | null>(null);
  useEffect(() => {
    drainRef.current = drain;
  }, [drain]);

  useEffect(() => {
    payloadFingerprintRef.current = payloadFingerprint;
  }, [payloadFingerprint]);

  useEffect(() => {
    onSyncDirtyChange?.(syncBusy || payloadFingerprint !== syncedFingerprint);
  }, [onSyncDirtyChange, payloadFingerprint, syncBusy, syncedFingerprint]);

  // 同步失敗後整條同步就停住，而清除錯誤只發生在成功路徑——等於一次失敗就要整頁重載
  // （Codex 第四輪）。改為店員一動購物車就清掉舊錯誤重試：那是他唯一能做的補救動作，
  // 而且再失敗會馬上再顯示，不會把真的故障蓋掉。
  const lastTriedFingerprint = useRef(payloadFingerprint);
  useEffect(() => {
    if (payloadFingerprint === lastTriedFingerprint.current) return;
    lastTriedFingerprint.current = payloadFingerprint;
    setSyncError((previous) => (previous === null ? previous : null));
  }, [payloadFingerprint]);

  useEffect(() => {
    const terminalRow = terminal.data;
    // 凍結/付款中的購物車本來就不可修改，硬推只會拿到 409 並把同步永久停住——
    // POS 重整後遇到既有 FROZEN 購物車會穩定踩到（Codex 第四輪）。
    const cartLocked =
      current.data?.status === "FROZEN" ||
      current.data?.status === "PROCESSING" ||
      current.data?.status === "PAYMENT_UNCERTAIN";
    if (
      !hydrated ||
      !terminalRow?.paired_kiosk ||
      !ready ||
      cartLocked ||
      syncError !== null
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      const latest = payload.current;
      const fingerprint = payloadFingerprint;
      pending.current =
        latest.lines.length > 0
          ? {
              kind: "UPSERT",
              fingerprint,
              lines: latest.lines,
              buyerContactId: latest.buyerContactId,
              tenders: latest.tenders,
              adjustments: latest.adjustments,
              serviceMode: latest.serviceMode,
              tableNo: latest.tableNo,
            }
          : { kind: "CANCEL", fingerprint };
      setSyncBusy(true);
      void drain(terminalRow);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [
    buyerContactId,
    // 購物車狀態要進相依：凍結解除（撤回簽署）後必須重新跑這條 effect 恢復同步，
    // 否則店員撤回之後畫面就再也推不上去了（eslint 的 exhaustive-deps 抓到）。
    current.data?.status,
    drain,
    hydrated,
    payloadFingerprint,
    ready,
    syncError,
    terminal.data,
  ]);

  const pair = useMutation({
    mutationFn: async (code: string) => {
      if (!terminal.data) throw new Error("POS 櫃檯尚未就緒");
      const { data, error } = await api.POST(
        "/api/v1/customer-display/terminals/{terminal_id}/pair",
        {
          params: { path: { terminal_id: terminal.data.id } },
          body: { pairing_code: code },
        },
      );
      if (!data) {
        const detail =
          error && typeof error === "object" && "detail" in error
            ? String(error.detail)
            : "配對失敗";
        throw new Error(detail);
      }
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["customer-display", "terminal"], data);
      setPairingCode("");
    },
  });

  function submitPair(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (/^\d{6}$/.test(pairingCode)) pair.mutate(pairingCode);
  }

  if (terminal.isPending) {
    return <div className="pos-kiosk-status is-muted">顧客螢幕設定載入中…</div>;
  }
  if (terminal.isError || !terminal.data) {
    return (
      <div className="pos-kiosk-status is-error" role="alert">
        顧客螢幕設定無法連線；一般付款仍可繼續，購物金暫不可用。
      </div>
    );
  }
  if (!terminal.data.paired_kiosk) {
    return (
      <form className="pos-kiosk-status is-warning" onSubmit={submitPair}>
        <strong>顧客螢幕尚未配對</strong>
        <label>
          <span className="sr-only">顧客螢幕配對碼</span>
          <input
            inputMode="numeric"
            pattern="\d{6}"
            maxLength={6}
            placeholder="輸入 6 碼"
            value={pairingCode}
            onChange={(event) =>
              setPairingCode(event.target.value.replace(/\D/g, "").slice(0, 6))
            }
          />
        </label>
        <button type="submit" className="btn-secondary" disabled={pair.isPending}>
          {pair.isPending ? "配對中…" : "配對"}
        </button>
        {pair.isError && <span role="alert">{pair.error.message}</span>}
      </form>
    );
  }
  const kiosk = terminal.data.paired_kiosk;
  const visibleRevision = Math.max(
    syncedRevision ?? 0,
    current.data?.revision ?? 0,
  );
  return (
    <div className={`pos-kiosk-status ${kiosk.online ? "is-online" : "is-warning"}`}>
      <span className="pos-kiosk-dot" aria-hidden />
      <strong>{kiosk.online ? "顧客螢幕已連線" : "顧客螢幕離線"}</strong>
      <span>{kiosk.label}</span>
      {visibleRevision > 0 && <span>購物車版本 {visibleRevision}</span>}
      {syncError && (
        <span role="alert" className="form-error">
          {syncError}
        </span>
      )}
    </div>
  );
}
