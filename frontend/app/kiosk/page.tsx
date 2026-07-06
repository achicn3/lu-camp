"use client";
// 手持簽署裝置主頁（docs/23 K3）：KIOSK 帳號登入 → 每 2 秒輪詢待簽任務 → 顯示切結書/
// 品項金額/會員資料 → 客人選撥款（AFFIDAVIT 二選一，D7）→ 手寫簽名 → 送出。簽名綁內容快照：
// 顯示的就是客人簽的那份（content 由店員端凍結）。送出後回待機，等下一張任務。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { login } from "@/lib/auth";
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

export default function KioskPage() {
  const token = useSyncExternalStore(subscribeToken, getToken, () => null);
  const hydrated = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );

  if (!hydrated) return null;
  if (token === null) return <KioskLogin />;
  return <KioskConsole />;
}

// ── 裝置登入（KIOSK 帳號，一次長駐）──────────────────────────────────────
function KioskLogin() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
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
    if (!result.ok) setError(result.message);
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

// ── 已登入主控：輪詢 → 待機/任務 ────────────────────────────────────────
function KioskConsole() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["kiosk", "current"],
    queryFn: fetchCurrentTask,
    refetchInterval: 2000,
    refetchOnWindowFocus: true,
  });

  if (error instanceof Error && error.message === "FORBIDDEN") {
    return (
      <main className="kiosk-standby">
        <div className="kiosk-standby-inner">
          <h1 className="kiosk-standby-title">此裝置非簽署帳號</h1>
          <p className="kiosk-standby-sub">請以 KIOSK 簽署帳號重新登入。</p>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              // 認證邊界：清整個快取再清 token，避免重登期間閃現舊快照/店務殘留（Codex K3）。
              queryClient.clear();
              clearToken();
            }}
          >
            重新登入
          </button>
        </div>
      </main>
    );
  }

  if (!data) return <Standby />;
  // key=task.id：任務換人即重新掛載，本地狀態（簽名/勾選/撥款）自然重置，
  // 不需 effect 手動清（避免沿用上一位客人的確認旗標）。
  return <TaskScreen key={data.id} task={data} />;
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

function TaskScreen({ task }: { task: KioskTask }) {
  const queryClient = useQueryClient();
  const canvasRef = useRef<SignatureCanvasHandle>(null);
  const [hasInk, setHasInk] = useState(false);
  const [payout, setPayout] = useState<"CASH" | "STORE_CREDIT" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [agreed, setAgreed] = useState(false);
  // 任務已被店員作廢/取代（409）：終態，鎖住送出，靠輪詢帶回待機/新任務。
  const [superseded, setSuperseded] = useState(false);

  const needsPayout = PAYOUT_KINDS.has(task.kind);
  const needsAgreement = task.agreement_body !== null;

  const canSubmit =
    hasInk &&
    !submitting &&
    !superseded &&
    (!needsPayout || payout !== null) &&
    (!needsAgreement || agreed);

  async function submit() {
    const image = canvasRef.current?.toBase64();
    if (!image) {
      setError("簽名太少，請簽得更完整（或清除重簽）。");
      return;
    }
    setSubmitting(true);
    setError(null);
    const { response } = await api.POST("/api/v1/kiosk/tasks/{task_id}/sign", {
      params: { path: { task_id: task.id } },
      body: { signature_image_base64: image, chosen_payout: needsPayout ? payout : null },
    });
    setSubmitting(false);
    if (!response.ok) {
      if (response.status === 409) {
        // 任務已被店員作廢/取代（反悔或改內容重推）：標記終態、鎖住送出，
        // 並立即失效輪詢查詢 → 下一輪回待機或帶出新任務（Codex K3 medium）。
        setSuperseded(true);
        setError("此項目已由店員更新，請依店員指示，稍候將顯示最新內容。");
        void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
      } else if (response.status === 422) {
        setError("簽名無法辨識，請清除後簽得更完整。");
      } else {
        setError("送出失敗，請再試一次或請店員協助。");
      }
      return;
    }
    setDone(true);
  }

  if (done) {
    return (
      <main className="kiosk-thanks">
        <div className="kiosk-thanks-inner">
          <div className="kiosk-thanks-check" aria-hidden>
            ✓
          </div>
          <h1 className="kiosk-thanks-title">已完成簽署</h1>
          <p className="kiosk-standby-sub">感謝您，請將裝置交回給店員。</p>
        </div>
      </main>
    );
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
                onClick={() => setPayout("CASH")}
              >
                現金
              </button>
              <button
                type="button"
                className={payoutClass(payout === "STORE_CREDIT")}
                onClick={() => setPayout("STORE_CREDIT")}
              >
                購物金
              </button>
            </div>
          </div>
        )}

        <div className="kiosk-signature">
          <h2 className="kiosk-section-title">簽名確認</h2>
          <SignatureCanvas ref={canvasRef} onInkChange={setHasInk} />
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
