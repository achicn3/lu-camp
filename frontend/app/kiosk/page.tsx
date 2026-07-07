"use client";
// 手持簽署裝置主頁（docs/23 K3）：KIOSK 帳號登入 → 每 2 秒輪詢待簽任務 → 顯示切結書/
// 品項金額/會員資料 → 客人選撥款（AFFIDAVIT 二選一，D7）→ 手寫簽名 → 送出。簽名綁內容快照：
// 顯示的就是客人簽的那份（content 由店員端凍結）。送出後回待機，等下一張任務。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { login, readTokenRole, verifyStaffCredentials } from "@/lib/auth";
import { clearToken, getToken, subscribeToken } from "@/lib/token";

import { SignatureCanvas, type SignatureCanvasHandle } from "./SignatureCanvas";

type KioskTask = NonNullable<
  Awaited<ReturnType<typeof fetchCurrentTask>>
>;

async function fetchCurrentTask() {
  const { data, response } = await api.GET("/api/v1/kiosk/tasks/current");
  if (!response.ok) {
    // 403：此裝置非 KIOSK 帳號（後端 D4 圍堵）；往上拋讓 UI 提示重登。
    throw new Error(response.status === 403 ? "FORBIDDEN" : "FETCH_FAILED");
  }
  return data ?? null;
}

const emptySubscribe = () => () => {};

// 交回鎖持久化（Codex K3 第六輪 high）：簽署完成的隱私鎖不可只存記憶體——瀏覽器重整/
// 重掛會以 completed=false 重啟輪詢、把下一位任務顯示給前一位客人。改存 localStorage，
// 僅由店員解鎖清除。單店單裝置故用單一鍵。
const HANDOFF_KEY = "lu-camp.kiosk-handoff";
function readHandoffLock(): boolean {
  return typeof window !== "undefined" && window.localStorage.getItem(HANDOFF_KEY) === "1";
}
function setHandoffLock(on: boolean): void {
  if (typeof window === "undefined") return;
  if (on) window.localStorage.setItem(HANDOFF_KEY, "1");
  else window.localStorage.removeItem(HANDOFF_KEY);
}

// 曖昧簽署鎖（Codex K3 第七輪 high）：POST **送出前**即持久化——若後端已寫入但回應遺失、
// 客人又在重送前重整，重掛後據此進入店員解鎖的恢復畫面、絕不恢復輪詢顯示下一位任務。
// 明確結果（成功/HTTP 錯誤）才清除；thrown（連線失敗）保留。
const SIGNING_LOCK_KEY = "lu-camp.kiosk-signing";
function readSigningLock(): boolean {
  return typeof window !== "undefined" && window.localStorage.getItem(SIGNING_LOCK_KEY) === "1";
}
function setSigningLock(on: boolean): void {
  if (typeof window === "undefined") return;
  if (on) window.localStorage.setItem(SIGNING_LOCK_KEY, "1");
  else window.localStorage.removeItem(SIGNING_LOCK_KEY);
}

// LAN（http，非安全來源）下 crypto.randomUUID 可能不存在——提供退回實作，供簽名冪等鍵用。
function newIdempotencyKey(): string {
  const c = typeof crypto !== "undefined" ? crypto : undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export default function KioskPage() {
  const token = useSyncExternalStore(subscribeToken, getToken, () => null);
  const hydrated = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );

  if (!hydrated) return null;
  if (token === null) return <KioskLogin />;
  // 同步（本地解碼、不需連線）攔非 KIOSK token：客人裝置上若殘留有效店務 token，
  // 絕不掛載 console（否則該 token 仍可被導去店務殼）——直接清除並回裝置登入
  // （Codex K3 high；後端 403 清除為次要防線）。
  if (readTokenRole(token) !== "KIOSK") return <NonKioskGate />;
  return <KioskConsole />;
}

// 客人裝置上出現非 KIOSK token：清 token+快取後回裝置登入（清除觸發 token 變更 → 重繪）。
function NonKioskGate() {
  const queryClient = useQueryClient();
  useEffect(() => {
    queryClient.clear();
    clearToken();
  }, [queryClient]);
  return <KioskLogin initialError="此裝置僅限 KIOSK 簽署帳號登入。" />;
}

// ── 裝置登入（KIOSK 帳號，一次長駐）──────────────────────────────────────
function KioskLogin({ initialError = null }: { initialError?: string | null }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(initialError);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setError(null);
    // 進入/切換 KIOSK 是一次認證邊界轉換：清整個 QueryClient，避免此 SPA 內殘留的
    // 店務頁快取（非 kiosk 鍵）在客人面向裝置上被看到，也不閃現前一位客人的任務
    // 快照（Codex K3 medium；比照 (authed) 登入/登出清快取）。
    queryClient.clear();
    const result = await login(String(form.get("username")), String(form.get("password")));
    setSubmitting(false);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    // 只有 KIOSK 帳號可留在此裝置：誤用店務帳號（MANAGER/CLERK）登入會把有效店務
    // token 留在客人面向裝置上、可被導去店務殼——故非 KIOSK 一律立刻清除並提示
    // （Codex K3 high）。
    if (readTokenRole() !== "KIOSK") {
      clearToken();
      queryClient.clear();
      setError("此帳號非簽署裝置帳號，請以本店 KIOSK 簽署帳號登入。");
    }
  }

  return (
    <main className="kiosk-login">
      <form className="kiosk-login-card" onSubmit={onSubmit}>
        <h1 className="kiosk-login-title">簽署裝置設定</h1>
        <p className="kiosk-login-sub">請以本店簽署裝置帳號登入（一次登入、長期使用）</p>
        <label className="field">
          <span className="field-label">帳號</span>
          <input name="username" autoComplete="username" required autoFocus />
        </label>
        <label className="field">
          <span className="field-label">密碼</span>
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? "登入中…" : "啟用裝置"}
        </button>
      </form>
    </main>
  );
}

// ── 已登入主控：輪詢 → 待機/任務/交回 ────────────────────────────────────
function KioskConsole() {
  const queryClient = useQueryClient();
  // 簽署完成後暫停輪詢並停在「交回店員」畫面（Codex K3 high）：否則店員在客人尚未
  // 交回裝置前建立下一張任務，輪詢會讓上一位客人看到下一位客人的內容/個資。
  // 初值讀持久化交回鎖：重整/重掛後若上一位尚未由店員解鎖，仍停在交回畫面。
  const [completed, setCompleted] = useState(readHandoffLock);
  // 曖昧簽署恢復（Codex K3 第七輪 high）：重掛時若持久化簽署鎖仍在（thrown 後未收斂又重整），
  // 進入店員解鎖恢復畫面、不輪詢——避免把下一位任務顯示給前一位客人。
  const [recovering, setRecovering] = useState(() => readSigningLock() && !readHandoffLock());
  // 簽名送出進行中亦暫停輪詢（Codex K3 high）：否則 POST 尚未回應期間，輪詢可能因
  // 店員重推而換掉 data、使 key=id 重掛出下一張任務，讓前一位客人看到他人內容。
  // 僅暫停 enabled 不夠——POST 前已在途的 refetch 仍可能回填快取；故簽名期間另以
  // frozenTask 凍結畫面上的任務、並 cancelQueries 中止在途請求（Codex K3 第五輪 high）。
  const [frozenTask, setFrozenTask] = useState<KioskTask | null>(null);
  const signing = frozenTask !== null;
  const paused = completed || signing || recovering;
  const { data, error } = useQuery({
    queryKey: ["kiosk", "current"],
    queryFn: fetchCurrentTask,
    refetchInterval: paused ? false : 2000,
    refetchOnWindowFocus: !paused,
    enabled: !paused,
  });

  function onSigningChange(active: boolean, task?: KioskTask) {
    if (active && task) {
      setFrozenTask(task); // 凍結顯示的任務
      void queryClient.cancelQueries({ queryKey: ["kiosk", "current"] }); // 中止在途 refetch
    } else {
      setFrozenTask(null);
    }
  }

  const forbidden = error instanceof Error && error.message === "FORBIDDEN";
  // 非 KIOSK token 落在客人裝置上：立刻清 token+快取（不等點按），避免店務殼外洩
  // （Codex K3 high）。清除後 token→null，KioskPage 會切回裝置登入畫面。
  useEffect(() => {
    if (forbidden) {
      queryClient.clear();
      clearToken();
    }
  }, [forbidden, queryClient]);

  if (recovering) {
    return (
      <StaffGate
        variant="recover"
        title="上一筆簽署尚未確認"
        message="請店員確認此筆是否已簽署後解鎖，再接續作業。"
        unlockLabel="店員確認並解鎖"
        onReset={() => {
          // 店員已確認：清簽署鎖＋當前任務快取再恢復輪詢。
          setSigningLock(false);
          queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
          setRecovering(false);
        }}
      />
    );
  }
  if (completed) {
    return (
      <StaffGate
        variant="done"
        title="已完成簽署"
        message="感謝您，請將裝置交回給店員。"
        unlockLabel="店員解鎖，接續下一位"
        onReset={() => {
          // 店員解鎖：清持久化交回鎖＋暫存的當前任務再恢復輪詢，避免恢復瞬間閃現舊任務。
          setHandoffLock(false);
          queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
          setCompleted(false);
        }}
      />
    );
  }
  // 簽名進行中一律顯示凍結的任務（忽略在途 refetch 回填的新 data），避免 POST 途中換人。
  const shown = frozenTask ?? data;
  if (forbidden || !shown) return <Standby />; // forbidden 為短暫態；effect 清 token 後回登入
  // key=task.id：任務換人即重新掛載，本地狀態（簽名/勾選/撥款）自然重置，
  // 不需 effect 手動清（避免沿用上一位客人的確認旗標）。
  return (
    <TaskScreen
      key={shown.id}
      task={shown}
      onSigningChange={onSigningChange}
      onComplete={() => {
        setHandoffLock(true); // 持久化交回鎖：重整也停在交回畫面，須店員解鎖
        setFrozenTask(null);
        setCompleted(true);
      }}
    />
  );
}

// 店員帳密解鎖畫面（Codex K3 high）：交回鎖／曖昧簽署恢復皆須現場店務員帳密授權，避免
// 客人自行點按解鎖看到下一位客人內容。驗證不持久化 token（裝置身分仍為 KIOSK）。
function StaffGate({
  variant,
  title,
  message,
  unlockLabel,
  onReset,
}: {
  variant: "done" | "recover";
  title: string;
  message: string;
  unlockLabel: string;
  onReset: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);

  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setVerifying(true);
    setError(null);
    const ok = await verifyStaffCredentials(
      String(form.get("username")),
      String(form.get("password")),
    );
    setVerifying(false);
    if (ok) onReset();
    else setError("店務員帳密不正確，無法解鎖。");
  }

  return (
    <main className="kiosk-thanks">
      <div className="kiosk-thanks-inner">
        <div
          className={variant === "done" ? "kiosk-thanks-check" : "kiosk-thanks-check kiosk-thanks-check--warn"}
          aria-hidden
        >
          {variant === "done" ? "✓" : "!"}
        </div>
        <h1 className="kiosk-thanks-title">{title}</h1>
        <p className="kiosk-standby-sub">{message}</p>
        {!showForm ? (
          <button type="button" className="btn-secondary" onClick={() => setShowForm(true)}>
            {unlockLabel}
          </button>
        ) : (
          <form className="kiosk-unlock-form" onSubmit={unlock}>
            <label className="field">
              <span className="field-label">店員帳號</span>
              <input name="username" autoComplete="off" required autoFocus />
            </label>
            <label className="field">
              <span className="field-label">密碼</span>
              <input name="password" type="password" autoComplete="off" required />
            </label>
            {error !== null && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
            <button type="submit" className="btn-primary" disabled={verifying}>
              {verifying ? "驗證中…" : "解鎖"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}

function Standby() {
  return (
    <main className="kiosk-standby">
      <div className="kiosk-standby-inner">
        <h1 className="kiosk-standby-title">露營二手</h1>
        <p className="kiosk-standby-sub">請稍候，店員將為您送出待確認的項目。</p>
        <div className="kiosk-standby-dot" aria-hidden />
      </div>
    </main>
  );
}

// ── 任務畫面：切結書 + 明細 + 撥款 + 簽名 ────────────────────────────────
const PAYOUT_KINDS = new Set(["ACQUISITION_AFFIDAVIT"]);

function TaskScreen({
  task,
  onComplete,
  onSigningChange,
}: {
  task: KioskTask;
  onComplete: () => void;
  onSigningChange: (signing: boolean, task?: KioskTask) => void;
}) {
  const queryClient = useQueryClient();
  const canvasRef = useRef<SignatureCanvasHandle>(null);
  // 每張任務一把冪等鍵（隨此 TaskScreen 掛載生成、跨重試不變）：回應遺失後以同鍵重送，
  // 後端回放同結果而非 409（Codex K3 第六輪）。key=task.id 換任務即重掛→自然換新鍵。
  const idempotencyKey = useRef<string>(newIdempotencyKey());
  // 首次送出凍結的 payload（重試沿用同一份，避免在途變更造成同鍵不同指紋 409）。
  const submittedPayload = useRef<{ image: string; payout: "CASH" | "STORE_CREDIT" | null } | null>(
    null,
  );
  const [hasInk, setHasInk] = useState(false);
  const [payout, setPayout] = useState<"CASH" | "STORE_CREDIT" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agreed, setAgreed] = useState(false);
  // 任務已被店員作廢/取代（409）：終態，鎖住送出，靠輪詢帶回待機/新任務。
  const [superseded, setSuperseded] = useState(false);
  // 曾遇 thrown（曖昧提交）：鎖住撥款/同意/清除，令重送必為同一 payload → 後端同鍵同指紋
  // 回放成功（改了 payload 會撞不同指紋 409）（Codex K3 第七輪）。
  const [payloadLocked, setPayloadLocked] = useState(false);

  const needsPayout = PAYOUT_KINDS.has(task.kind);
  const needsAgreement = task.agreement_body !== null;
  // 送出在途或曖昧鎖定時，撥款/同意/簽名一律不可改（重試須與已送出的 payload 一致）。
  const controlsLocked = submitting || payloadLocked;

  const canSubmit =
    hasInk &&
    !submitting &&
    !superseded &&
    (!needsPayout || payout !== null) &&
    (!needsAgreement || agreed);

  async function submit() {
    // 凍結「首次送出」的 payload 並於重試沿用同一份（Codex K3 第八輪 high）：否則在 POST
    // 在途期間客人改撥款/重畫，重試會以同鍵送出不同內容 → 撞不同指紋 409 → 誤判 superseded
    // 清鎖恢復輪詢。捕捉於 ref、送出即鎖控制項（submitting||payloadLocked），杜絕在途變更。
    if (submittedPayload.current === null) {
      const image = canvasRef.current?.toBase64();
      if (!image) {
        setError("簽名太少，請簽得更完整（或清除重簽）。");
        return;
      }
      submittedPayload.current = { image, payout: needsPayout ? payout : null };
    }
    const frozen = submittedPayload.current;
    setSubmitting(true);
    setPayloadLocked(true); // 送出即鎖：POST 在途期間不得改動撥款/同意/簽名
    setError(null);
    // 送出期間凍結父層任務並中止在途輪詢，避免任務在 POST 途中被換掉（Codex K3 第五輪）。
    onSigningChange(true, task);
    // 送出「前」即持久化簽署鎖：即使 POST 已寫入但回應遺失、客人又重整，重掛也會進恢復畫面
    // 而非恢復輪詢（Codex K3 第七輪 high）。明確結果才清除。
    setSigningLock(true);
    // outcome 三分類（Codex K3 第九輪 high）：
    //  - definitive：後端**確定**未寫入或已終態（409/4xx）→ 清鎖、恢復輪詢。
    //  - ambiguous：**可能已寫入**（5xx / thrown）→ 保持凍結、不恢復輪詢，同鍵重送或店員解鎖收斂。
    //  - ok：成功。
    let outcome: "ok" | "definitive" | "ambiguous" = "ambiguous";
    try {
      const { response } = await api.POST("/api/v1/kiosk/tasks/{task_id}/sign", {
        params: { path: { task_id: task.id } },
        body: {
          signature_image_base64: frozen.image,
          chosen_payout: frozen.payout,
          idempotency_key: idempotencyKey.current,
        },
      });
      if (response.ok) {
        outcome = "ok";
      } else if (response.status >= 500) {
        // 5xx（500/502/503/504）不是「未寫入」的證明——可能 commit 後才失敗/序列化失敗/
        // 閘道逾時。當作曖昧，同 thrown 處理（保持凍結、不恢復輪詢）（Codex K3 第九輪 high）。
        outcome = "ambiguous";
        setError("伺服器忙線，請再按一次「確認並送出」（系統會避免重複簽名）。");
      } else {
        outcome = "definitive";
        if (response.status === 409) {
          // 任務已被店員作廢/取代（反悔或改內容重推）：標記終態、鎖住送出，
          // 並立即失效輪詢查詢 → 下一輪回待機或帶出新任務（Codex K3 medium）。
          setSuperseded(true);
          setError("此項目已由店員更新，請依店員指示，稍候將顯示最新內容。");
          void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
        } else if (response.status === 422) {
          // 後端明確拒收此影像：解鎖並清凍結 payload，讓客人重新簽（回應已達，無曖昧）。
          setPayloadLocked(false);
          submittedPayload.current = null;
          setError("簽名無法辨識，請清除後簽得更完整。");
        } else {
          // 其他 4xx（客端錯誤，確定未寫入）：解鎖允許重簽。
          setPayloadLocked(false);
          submittedPayload.current = null;
          setError("送出失敗，請再試一次或請店員協助。");
        }
      }
    } catch {
      // 網路/LAN 失敗（fetch reject）：後端**可能已寫入但回應遺失**。不恢復輪詢（保持
      // 凍結、鎖住 POST 途中的隱私邊界），提示以同一冪等鍵再送一次——若已寫入則回放成功、
      // 否則正常簽（Codex K3 第六輪 high）。payload 已於送出時鎖定並凍結，重送必為同內容。
      outcome = "ambiguous";
      setError("連線不穩，請再按一次「確認並送出」（系統會避免重複簽名）。");
    } finally {
      setSubmitting(false);
      // definitive 才清鎖恢復輪詢；ambiguous（5xx/thrown）保留簽署鎖與凍結，避免恢復輪詢
      // 把下一位任務顯示給前一位客人——由同鍵重送 或 重整後店員解鎖收斂。
      if (outcome === "definitive") {
        setSigningLock(false);
        onSigningChange(false);
      }
    }
    if (outcome === "ok") {
      // 成功：清簽署鎖，交由 KioskConsole 顯示「交回店員」並暫停輪詢（不在此本地顯示完成
      // 畫面，避免輪詢在客人交回前帶出下一位客人的任務）。
      setSigningLock(false);
      onComplete();
    }
  }

  return (
    <main className="kiosk-task">
      <header className="kiosk-task-header">
        <h1 className="kiosk-task-title">{taskHeading(task.kind)}</h1>
      </header>

      <section className="kiosk-task-body">
        <ContentSnapshot content={task.content} />

        {needsAgreement && (
          <div className="kiosk-agreement">
            <h2 className="kiosk-agreement-title">{task.agreement_title}</h2>
            <div className="kiosk-agreement-body">{task.agreement_body}</div>
            <label className="kiosk-agree-check">
              <input
                type="checkbox"
                checked={agreed}
                disabled={controlsLocked}
                onChange={(e) => setAgreed(e.target.checked)}
              />
              <span>本人已閱讀並同意上述切結書及條款內容</span>
            </label>
          </div>
        )}

        {needsPayout && (
          <div className="kiosk-payout">
            <h2 className="kiosk-section-title">請選擇收款方式</h2>
            <div className="kiosk-payout-options">
              <button
                type="button"
                className={payoutClass(payout === "CASH")}
                disabled={controlsLocked}
                onClick={() => setPayout("CASH")}
              >
                現金
              </button>
              <button
                type="button"
                className={payoutClass(payout === "STORE_CREDIT")}
                disabled={controlsLocked}
                onClick={() => setPayout("STORE_CREDIT")}
              >
                購物金
              </button>
            </div>
          </div>
        )}

        <div className="kiosk-signature">
          <h2 className="kiosk-section-title">簽名確認</h2>
          <SignatureCanvas ref={canvasRef} onInkChange={setHasInk} locked={controlsLocked} />
        </div>
      </section>

      <footer className="kiosk-task-footer">
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button
          type="button"
          className="btn-primary kiosk-submit"
          disabled={!canSubmit}
          onClick={submit}
        >
          {submitting ? "送出中…" : "確認並送出"}
        </button>
      </footer>
    </main>
  );
}

function taskHeading(kind: string): string {
  switch (kind) {
    case "ACQUISITION_AFFIDAVIT":
      return "收購確認與切結";
    case "STORE_CREDIT_USE":
      return "購物金使用確認";
    case "TRANSACTION_ACK":
      return "交易紀錄簽收";
    default:
      return "簽署確認";
  }
}

function payoutClass(active: boolean): string {
  return active ? "kiosk-payout-btn kiosk-payout-btn--active" : "kiosk-payout-btn";
}

// content 為店員端凍結的顯示快照（自由 dict）：優雅呈現已知欄位（品項清單＋常見純量）。
const CONTENT_LABELS: Record<string, string> = {
  seller_name: "姓名",
  member_name: "會員",
  national_id_masked: "身分證字號",
  phone: "電話",
  address: "住址",
  total: "合計金額",
  deduct: "扣抵購物金",
  balance: "購物金餘額",
  balance_after: "扣抵後餘額",
};

// 客人簽的是完整 JSON 快照，故此處**窮舉渲染**所有欄位、不靜默丟棄任何鍵
// （Codex K3 high：簽到沒看到的內容＝證據風險）。已知鍵給中文標籤與金額格式，
// 未知鍵照原樣列出；巢狀物件/陣列（items 以外）以可讀字串呈現。
function ContentSnapshot({ content }: { content: Record<string, unknown> }) {
  const itemsIsArray = Array.isArray(content.items);
  const items = itemsIsArray ? (content.items as unknown[]) : [];
  // 僅在 items 真的以陣列渲染時，才將它排除於一般欄位；若 items 非陣列（schema 漂移/
  // 錯誤生產者），仍以一般欄位 renderValue 列出，絕不靜默丟棄客人所簽內容（Codex K3 高）。
  const rest = Object.entries(content).filter(([key]) => key !== "items" || !itemsIsArray);

  return (
    <div className="kiosk-snapshot">
      {items.length > 0 && (
        <table className="kiosk-items">
          <thead>
            <tr>
              <th>品項</th>
              <th className="kiosk-items-amount">金額</th>
            </tr>
          </thead>
          <tbody>
            {items.map((raw, i) => {
              const item = (raw ?? {}) as Record<string, unknown>;
              // name/amount 以外的品項欄位一併呈現，避免遺漏客人所簽內容。
              const extra = Object.entries(item).filter(([k]) => k !== "name" && k !== "amount");
              return (
                <tr key={i}>
                  <td>
                    {String(item.name ?? "—")}
                    {extra.length > 0 && (
                      <span className="kiosk-item-extra">
                        {extra.map(([k, v]) => `${CONTENT_LABELS[k] ?? k}：${renderValue(v)}`).join("；")}
                      </span>
                    )}
                  </td>
                  <td className="kiosk-items-amount">{formatAmount(item.amount)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {rest.length > 0 && (
        <dl className="kiosk-fields">
          {rest.map(([key, value]) => (
            <div className="kiosk-field-row" key={key}>
              <dt>{CONTENT_LABELS[key] ?? key}</dt>
              <dd>{isAmountKey(key) ? formatAmount(value) : renderValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function isAmountKey(key: string): boolean {
  return ["total", "deduct", "balance", "balance_after"].includes(key);
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string" || typeof value === "number") return String(value);
  // 巢狀物件/陣列：以 JSON 呈現，確保不遺漏客人所簽內容（寧可醜、不可漏）。
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatAmount(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const num = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(num)) return String(value);
  return `$${num.toLocaleString("zh-TW")}`;
}
