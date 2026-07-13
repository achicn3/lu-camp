// 收購/結帳等帶 Idempotency-Key 的請求：回應遺失/失敗時是否可安全「丟棄」凍結的冪等鍵。
//
// 只有在**確定後端未提交任何東西**時才可丟棄鍵、讓下次送出換新鍵：
// - 4xx 一般驗證/認證錯誤（400/401/403/422…）→ 後端於插入前即拒、必然未提交 → 可丟棄。
// - **409 衝突**→ 代表該鍵（或其切結）已屬於一筆**先前已提交**的收購（内容指紋不同才會撞）：
//   這是「先前已成功提交」的證據，**不可丟棄**——否則店員改了表單再送，會以新鍵建立第二筆
//   收購並重複撥款（docs/23 K4 第十六/十七輪）。保留鍵：同鍵重送要嘛冪等重放原單、要嘛續 409。
// - 5xx／逾時／網路中斷 → 曖昧（後端可能已提交才失敗回應）→ 不可丟棄、沿用同鍵交後端重放。
export function canDiscardIdempotencyKey(status: number): boolean {
  return status >= 400 && status < 500 && status !== 409;
}

// 未完成收購的冪等鍵須**跨重掛/重新整理/導覽**存活（Codex K4 第十八輪）：只存在 React ref 會
// 在頁面重掛時遺失，未簽收購（無 signature_task_id 可二次關聯）便會在重掛後以新鍵重複建單/
// 撥款。故送出前先寫入 localStorage 為唯一事實來源，成功或非衝突 4xx 才清；重掛後由此還原。
const PENDING_ACQ_IDEM_STORAGE_KEY = "lu-camp.acq-pending-idem";

// 記憶體後備（Codex K4 第二十輪）：localStorage.setItem 若因配額/隱私政策丟例外被吞掉，
// load 會讀不到而讓重試改鑄新鍵→重複建單。故一律先寫入模組層記憶體，保證**本 session 內**
// 的重試（含網路中斷/5xx 曖昧）沿用同鍵；localStorage 成功時另享跨重掛/重整持久。
let memoryPendingKey: string | null = null;

export function loadPendingAcqIdemKey(): string | null {
  try {
    const stored = globalThis.localStorage?.getItem(PENDING_ACQ_IDEM_STORAGE_KEY) ?? null;
    if (stored != null) return stored;
  } catch {
    // 讀取失敗：退回記憶體後備。
  }
  return memoryPendingKey;
}

// 回傳是否**持久化成功**（localStorage 寫入成功＝可跨重掛保護）；false 代表僅記憶體後備
// （本 session 仍防重複，但重整/重掛不保證），呼叫端可據此提示店員。
export function savePendingAcqIdemKey(key: string): boolean {
  memoryPendingKey = key;
  let durable = false;
  try {
    if (globalThis.localStorage != null) {
      globalThis.localStorage.setItem(PENDING_ACQ_IDEM_STORAGE_KEY, key);
      durable = true;
    }
  } catch {
    durable = false; // 配額/隱私政策：僅記憶體後備。
  }
  emitPendingAcqIdemChange();
  return durable;
}

export function clearPendingAcqIdemKey(): void {
  memoryPendingKey = null;
  try {
    globalThis.localStorage?.removeItem(PENDING_ACQ_IDEM_STORAGE_KEY);
  } catch {
    // 清除失敗不阻斷流程（記憶體後備已清）。
  }
  emitPendingAcqIdemChange();
}

// 讓收購頁能以 useSyncExternalStore **在掛載時就**反映是否有殘留鍵（hydration/hooks-lint 安全，
// 不需 mount-time effect）：save/clear 通知訂閱者重讀。第十九輪：殘留鍵須於掛載即擋下送出，
// 避免重掛後以相同內容靜默重放舊收購（開櫃/印舊標卻未建新單）。
const pendingAcqIdemListeners = new Set<() => void>();

function emitPendingAcqIdemChange(): void {
  for (const listener of pendingAcqIdemListeners) listener();
}

export function subscribePendingAcqIdemKey(onChange: () => void): () => void {
  pendingAcqIdemListeners.add(onChange);
  return () => pendingAcqIdemListeners.delete(onChange);
}

// getSnapshot：回傳字串鍵或 null（primitive，Object.is 穩定，不會觸發 useSyncExternalStore 迴圈）。
export function pendingAcqIdemKeySnapshot(): string | null {
  return loadPendingAcqIdemKey();
}

// SSR/hydration 首次快照一律 null，客端掛載後再切換為實際值——避免 hydration 不一致。
export function pendingAcqIdemKeyServerSnapshot(): string | null {
  return null;
}

// ── 分批收貨的 pending 冪等鍵（依採購單 id 分別保存）────────────────────────
// 收貨鍵只存 React ref 會在重新整理後遺失：若收貨已於後端 commit、回應途中斷線、店員重整後
// 再送，換新鍵便會被視為新收貨事件而重複入庫（Codex 第二輪 high）。故送出前先以「採購單 id」
// 為界持久化（localStorage＋記憶體後備，跨重整存活），成功或「確定未提交的 4xx」才清；
// 依 PO 分界避免某單殘留鍵誤用於另一單（後端會因 PO 不符回 409 擋下新單收貨）。
const RECEIVE_IDEM_PREFIX = "lu-camp.receive-pending-idem";
const memoryReceiveKeys = new Map<number, string>();

function receiveStorageKey(poId: number): string {
  return `${RECEIVE_IDEM_PREFIX}.${poId}`;
}

export function loadPendingReceiveIdemKey(poId: number): string | null {
  try {
    const stored = globalThis.localStorage?.getItem(receiveStorageKey(poId)) ?? null;
    if (stored != null) return stored;
  } catch {
    // 讀取失敗：退回記憶體後備。
  }
  return memoryReceiveKeys.get(poId) ?? null;
}

export function savePendingReceiveIdemKey(poId: number, key: string): void {
  memoryReceiveKeys.set(poId, key);
  try {
    globalThis.localStorage?.setItem(receiveStorageKey(poId), key);
  } catch {
    // 配額/隱私政策：僅記憶體後備（本 session 仍防重複，重整不保證）。
  }
}

export function clearPendingReceiveIdemKey(poId: number): void {
  memoryReceiveKeys.delete(poId);
  try {
    globalThis.localStorage?.removeItem(receiveStorageKey(poId));
  } catch {
    // 清除失敗不阻斷流程（記憶體後備已清）。
  }
}
