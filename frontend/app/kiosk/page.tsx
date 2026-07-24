"use client";
// 客顯／手持簽署共用頁：裝置 cookie 登入與配對，SSE 通知後全量重讀權威購物車；
// 使用購物金或收購任務時，沿用下方不可變內容快照與簽名流程。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type FormEvent,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { API_BASE_URL, kioskApi } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { verifyStaffCredentials } from "@/lib/auth";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

import { SignatureCanvas, type SignatureCanvasHandle } from "./SignatureCanvas";

type KioskDevice = components["schemas"]["KioskDeviceRead"];
type KioskCart = components["schemas"]["KioskCartSessionRead"];
type KioskTask = NonNullable<
  Awaited<ReturnType<typeof fetchCurrentTask>>
>;

async function fetchCurrentTask() {
  const { data, response } = await kioskApi.GET("/api/v1/kiosk/tasks/current");
  // 過渡資料若尚未配對到此裝置，視為無任務；不得因此退回 bearer 登入。
  if (response.status === 401 || response.status === 403 || response.status === 404) return null;
  if (!response.ok) {
    throw new Error("FETCH_FAILED");
  }
  return data ?? null;
}

const DEVICE_INSTALLATION_KEY = "lu-camp.kiosk.installation";
const DEVICE_CSRF_KEY = "lu-camp.kiosk.csrf";
const csrfListeners = new Set<() => void>();

function readCsrf(): string | null {
  return typeof window === "undefined"
    ? null
    : window.localStorage.getItem(DEVICE_CSRF_KEY);
}

function subscribeCsrf(listener: () => void): () => void {
  csrfListeners.add(listener);
  return () => csrfListeners.delete(listener);
}

function writeCsrf(value: string): void {
  window.localStorage.setItem(DEVICE_CSRF_KEY, value);
  csrfListeners.forEach((listener) => listener());
}

function installationId(): string {
  const existing = window.localStorage.getItem(DEVICE_INSTALLATION_KEY);
  if (existing) return existing;
  const generated = newIdempotencyKey();
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    generated,
  )
    ? generated
    : `${Date.now().toString(16).padStart(8, "0").slice(-8)}-0000-4000-8000-${Math.random()
        .toString(16)
        .slice(2)
        .padEnd(12, "0")
        .slice(0, 12)}`;
  window.localStorage.setItem(DEVICE_INSTALLATION_KEY, uuid);
  return uuid;
}

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

// 已認領任務釘選持久化（Codex K3 第十二輪 high）：釘選只存記憶體時，重整/重掛會清掉、
// 使空窗或閘門期間的下一張任務被當首張直接顯示、繞過店員確認。改存 localStorage，
// 僅由店員解鎖路徑更新/清除。
const ENGAGED_KEY = "lu-camp.kiosk-engaged";
function readEngagedTask(): number | null {
  if (typeof window === "undefined") return null;
  const v = window.localStorage.getItem(ENGAGED_KEY);
  if (v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function writeEngagedTask(id: number | null): void {
  if (typeof window === "undefined") return;
  if (id === null) window.localStorage.removeItem(ENGAGED_KEY);
  else window.localStorage.setItem(ENGAGED_KEY, String(id));
}

export default function KioskPage() {
  // SSR 與 hydration 都先使用 null；commit 後再由 React 讀取 localStorage，
  // 避免重新整理時伺服器登入畫面與瀏覽器客顯畫面不一致。
  const csrf = useSyncExternalStore(subscribeCsrf, readCsrf, () => null);
  const device = useQuery({
    queryKey: ["kiosk", "device"],
    enabled: csrf !== null,
    retry: false,
    // 登入回應是配對明碼唯一一次可取得的來源；先讓它完成首屏渲染，避免啟用 query 後
    // 立即 GET（只回裝置狀態、不保存明碼）在 React commit 前覆蓋快取。
    staleTime: 5_000,
    refetchInterval: (query) =>
      query.state.data?.paired_terminal == null ? 5000 : false,
    queryFn: async () => {
      const { data, response } = await kioskApi.GET("/api/v1/kiosk/device");
      if (response.status === 401) throw new Error("AUTH_REQUIRED");
      if (!data) throw new Error("無法讀取客顯裝置狀態");
      return data;
    },
  });

  if (csrf === null || device.isError) {
    return (
      <KioskLogin
        initialError={
          device.error instanceof Error && device.error.message !== "AUTH_REQUIRED"
            ? "裝置連線失敗，請重新登入。"
            : null
        }
      />
    );
  }
  if (!device.data) return <Standby message="正在確認裝置身分…" />;
  if (device.data.paired_terminal === null) {
    return <PairingScreen device={device.data} csrf={csrf} />;
  }
  return <KioskConsole csrf={csrf} />;
}

// ── 裝置登入（KIOSK 帳號，一次長駐）──────────────────────────────────────
function KioskLogin({
  initialError = null,
}: {
  initialError?: string | null;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(initialError);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setError(null);
    queryClient.clear();
    try {
      const { data, error: responseError, response } = await kioskApi.POST(
        "/api/v1/kiosk/device-sessions",
        {
          body: {
            username: String(form.get("username")),
            password: String(form.get("password")),
            installation_id: installationId(),
            label: String(form.get("label")),
          },
        },
      );
      if (!data) {
        const detail =
          responseError &&
          typeof responseError === "object" &&
          "detail" in responseError &&
          typeof responseError.detail === "string"
            ? responseError.detail
            : response.status === 429
              ? "嘗試次數過多，請稍後再試。"
              : "帳號或密碼錯誤。";
        setError(detail);
        return;
      }
      queryClient.setQueryData<KioskDevice>(["kiosk", "device"], data);
      writeCsrf(data.csrf_token);
    } catch {
      setError("無法連線到伺服器，請確認店內網路。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="kiosk-login">
      <form className="kiosk-login-card" onSubmit={onSubmit}>
        <h1 className="kiosk-login-title">顧客顯示裝置設定</h1>
        <p className="kiosk-login-sub">以本店 KIOSK 帳號啟用；登入後再與 POS 櫃檯配對。</p>
        <label className="field">
          <span className="field-label">帳號</span>
          <input name="username" autoComplete="username" required autoFocus />
        </label>
        <label className="field">
          <span className="field-label">密碼</span>
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <label className="field">
          <span className="field-label">裝置名稱</span>
          <input name="label" defaultValue="顧客平板" maxLength={100} required />
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

function PairingScreen({ device, csrf }: { device: KioskDevice; csrf: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  // 配對明碼只在登入／重新產生的 POST 回應出現，後端只落 hash；狀態輪詢回 null 時不可
  // 把仍有效的明碼從畫面抹掉。到 expires_at 時才由本地 UI 清除並要求重新產生。
  const [pairingCode, setPairingCode] = useState(device.pairing_code);
  const [pairingCodeExpiresAt, setPairingCodeExpiresAt] = useState(
    device.pairing_code_expires_at,
  );

  useEffect(() => {
    if (pairingCode === null || pairingCodeExpiresAt === null) return;
    const remaining = Date.parse(pairingCodeExpiresAt) - Date.now();
    const timer = window.setTimeout(() => {
      setPairingCode(null);
      setPairingCodeExpiresAt(null);
    }, Math.max(0, remaining));
    return () => window.clearTimeout(timer);
  }, [pairingCode, pairingCodeExpiresAt]);

  async function refreshCode() {
    setError(null);
    const { data } = await kioskApi.POST("/api/v1/kiosk/pairing-codes", {
      headers: { "X-CSRF-Token": csrf },
    });
    if (data) {
      setPairingCode(data.pairing_code);
      setPairingCodeExpiresAt(data.pairing_code_expires_at);
      queryClient.setQueryData(["kiosk", "device"], data);
    } else {
      setError("無法取得新配對碼，請確認店內網路。");
    }
  }

  return (
    <main className="kiosk-pairing">
      <section className="kiosk-pairing-card">
        <p className="kiosk-eyebrow">裝置已啟用 · {device.label}</p>
        <h1>連接您的 POS 櫃檯</h1>
        <p>請在 POS 輸入配對碼。配對完成後，此畫面會自動切換為顧客購物車。</p>
        {pairingCode ? (
          <output className="kiosk-pairing-code" aria-label="配對碼">
            {pairingCode}
          </output>
        ) : (
          <button type="button" className="btn-primary" onClick={refreshCode}>
            取得配對碼
          </button>
        )}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button type="button" className="btn-ghost" onClick={refreshCode}>
          重新產生配對碼
        </button>
      </section>
    </main>
  );
}

// ── 已配對主控：SSE 通知 → 全量重讀購物車／任務 → 待機／簽署／完成 ────────
function KioskConsole({ csrf }: { csrf: string }) {
  const queryClient = useQueryClient();
  const cart = useQuery({
    queryKey: ["kiosk", "cart"],
    queryFn: async () => {
      const { data, response } = await kioskApi.GET("/api/v1/kiosk/cart/current");
      if (!response.ok) throw new Error("無法讀取購物車");
      return data ?? null;
    },
  });
  const [streamConnected, setStreamConnected] = useState(false);
  const wakeLock = useRef<WakeLockSentinel | null>(null);

  useEffect(() => {
    const source = new EventSource(`${API_BASE_URL}/api/v1/kiosk/events`, {
      withCredentials: true,
    });
    const reload = () => {
      setStreamConnected(true);
      void queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
      void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
      void queryClient.invalidateQueries({ queryKey: ["kiosk", "device"] });
    };
    source.addEventListener("open", reload);
    source.addEventListener("state", reload);
    source.addEventListener("error", () => setStreamConnected(false));
    return () => source.close();
  }, [queryClient]);

  useEffect(() => {
    async function report() {
      const { response } = await kioskApi.POST("/api/v1/kiosk/heartbeat", {
        headers: { "X-CSRF-Token": csrf },
        body: {
          current_session_id: cart.data?.id ?? null,
          displayed_revision: cart.data?.revision ?? 0,
        },
      });
      if (response.status === 409) {
        await queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
      }
    }
    void report();
    const timer = window.setInterval(() => void report(), 15_000);
    return () => window.clearInterval(timer);
  }, [cart.data?.id, cart.data?.revision, csrf, queryClient]);

  useEffect(() => {
    async function keepAwake() {
      if (
        document.visibilityState === "visible" &&
        "wakeLock" in navigator &&
        wakeLock.current === null
      ) {
        try {
          wakeLock.current = await navigator.wakeLock.request("screen");
          wakeLock.current.addEventListener(
            "release",
            () => {
              wakeLock.current = null;
            },
            { once: true },
          );
        } catch {
          // 非 HTTPS、節電模式或裝置政策拒絕時，仍可由 kiosk/guided-access 作業設定常亮。
        }
      }
    }
    const visible = () => {
      if (document.visibilityState === "visible") {
        void queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
        void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
        void keepAwake();
      }
    };
    void keepAwake();
    document.addEventListener("visibilitychange", visible);
    return () => {
      document.removeEventListener("visibilitychange", visible);
      void wakeLock.current?.release();
      wakeLock.current = null;
    };
  }, [queryClient]);
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
  // 顯示中的任務一經呈現即「釘住」其 id（Codex K3 第十輪 high）：店員於客人尚未簽/交回前
  // 取消並改推另一張任務時，不得自動把新任務換到客人面前（可能是下一位客人的內容/個資）——
  // 需店員確認解鎖後才採用。以 React 官方「prop 變更時調整 state」模式於 render 中同步（非
  // effect、非 ref-in-render），待機清除、首張認領、不同任務不採用（交由 render 顯示閘門）。
  const [engagedTaskId, setEngagedTaskId] = useState<number | null>(readEngagedTask);
  // 任務被店員撤回後若立刻換成另一位顧客的任務，只保留新任務 id 作為交接閘門；
  // 新任務完整內容立刻自 Query cache 清除，待店員確認後才重新向後端讀取。
  const [pendingTaskId, setPendingTaskId] = useState<number | null>(null);
  const [syncedData, setSyncedData] = useState<KioskTask | null | undefined>(undefined);
  const [ackError, setAckError] = useState<string | null>(null);
  const acknowledging = useRef<number | null>(null);
  const signing = frozenTask !== null;
  const paused = completed || signing || recovering || pendingTaskId !== null;

  useEffect(() => {
    if (cart.data?.status !== "COMPLETED") return;
    const completedAt = Date.parse(cart.data.updated_at);
    const remaining = Number.isFinite(completedAt)
      ? Math.max(0, completedAt + 10_000 - Date.now())
      : 10_000;
    const timer = window.setTimeout(() => {
      // 成交後舊簽署已 CONSUMED；完成畫面到期時一併清掉交回鎖、任務釘選與
      // 本機快取，否則會從「交易已完成」退回已完成簽署閘門而無法回待機。
      setHandoffLock(false);
      setSigningLock(false);
      writeEngagedTask(null);
      queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
      setSyncedData(undefined);
      setAckError(null);
      setPendingTaskId(null);
      setEngagedTaskId(null);
      setRecovering(false);
      setCompleted(false);
      void queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [cart.data?.status, cart.data?.updated_at, queryClient]);

  const { data } = useQuery({
    queryKey: ["kiosk", "current"],
    queryFn: fetchCurrentTask,
    refetchOnWindowFocus: !paused,
    enabled: !paused,
  });

  useEffect(() => {
    if (paused || data?.status !== "PENDING" || acknowledging.current === data.id) return;
    acknowledging.current = data.id;
    setAckError(null);
    void kioskApi
      .POST("/api/v1/kiosk/tasks/{task_id}/ack", {
        params: { path: { task_id: data.id } },
        headers: { "X-CSRF-Token": csrf },
      })
      .then(async ({ response }) => {
        if (!response.ok) {
          setAckError("簽署任務已逾時或被撤回，正在重新載入…");
        }
        await queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
      })
      .catch(() => setAckError("無法確認簽署畫面，正在重新連線…"))
      .finally(() => {
        acknowledging.current = null;
      });
  }, [csrf, data?.id, data?.status, paused, queryClient]);

  // 於 render 中同步釘選（React 官方模式；React Query 結構共享使 data 參考在內容不變時穩定）。
  // **釘選只由店員解鎖清除、絕不因輪詢回 null 而清**（Codex K3 第十一輪 high）：否則
  // 「顯示 A → 店員取消 A（current=null）→ 建立 B」的空窗會讓 B 被當成首張任務直接顯示、
  // 繞過閘門。認領一次後即長駐，之後任何不同任務都會撞閘門。
  if (!paused && data !== syncedData) {
    const id = data?.id ?? null;
    if (id !== null && engagedTaskId === null) {
      setSyncedData(data);
      setEngagedTaskId(id); // 認領首張任務（僅在尚未認領時）
    } else if (
      id !== null &&
      engagedTaskId !== null &&
      id !== engagedTaskId &&
      pendingTaskId === null
    ) {
      // 不把下一位顧客的內容留在本地同步 state；只保留任務 id 供店員交接確認。
      setSyncedData(undefined);
      setPendingTaskId(id);
    } else {
      setSyncedData(data);
    }
    // id===null（暫無待簽）或不同任務：不動 engagedTaskId → render 顯示待機或店員確認閘門
  }

  // 釘選持久化到 localStorage（純副作用、無 setState）：重整/重掛後由初值還原、跨掛載守住
  // 閘門（Codex K3 第十二輪 high）。
  useEffect(() => {
    writeEngagedTask(engagedTaskId);
  }, [engagedTaskId]);

  useEffect(() => {
    if (pendingTaskId === null) return;
    queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
  }, [pendingTaskId, queryClient]);

  function onSigningChange(active: boolean, task?: KioskTask) {
    if (active && task) {
      setFrozenTask(task); // 凍結顯示的任務
      void queryClient.cancelQueries({ queryKey: ["kiosk", "current"] }); // 中止在途 refetch
    } else {
      setFrozenTask(null);
    }
  }

  // 付款階段是購物車的權威狀態，必須蓋過簽署交回鎖與仍為 SIGNED 的任務：
  // - PROCESSING 讓顧客知道店員正在收款；
  // - PAYMENT_UNCERTAIN 明確警告不得重複付款；
  // - COMPLETED 立即顯示成交結果。
  // 這些畫面只使用同一筆交易的後端最小快照，不會切換到下一位顧客的簽署內容。
  if (
    cart.data?.status === "PROCESSING" ||
    cart.data?.status === "PAYMENT_UNCERTAIN"
  ) {
    return <CartScreen cart={cart.data} streamConnected={streamConnected} />;
  }
  if (cart.data?.status === "COMPLETED") {
    return <CompletedSaleScreen cart={cart.data} />;
  }

  if (recovering) {
    return (
      <StaffGate
        variant="recover"
        title="上一筆簽署尚未確認"
        message="請店員確認此筆是否已簽署後解鎖，再接續作業。"
        unlockLabel="店員確認並解鎖"
        onReset={() => {
          // 店員已確認：清簽署鎖＋當前任務快取＋釘選再恢復輪詢。
          setSigningLock(false);
          setEngagedTaskId(null);
          setPendingTaskId(null);
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
          // 店員解鎖：清持久化交回鎖＋當前任務快取＋釘選再恢復輪詢，避免恢復瞬間閃現舊任務。
          setHandoffLock(false);
          setEngagedTaskId(null);
          setPendingTaskId(null);
          queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
          setCompleted(false);
        }}
      />
    );
  }
  if (pendingTaskId !== null) {
    return (
      <StaffGate
        variant="recover"
        title="任務已更新"
        message="內容已由店員更新，請店員確認後解鎖再交予客人。"
        unlockLabel="店員確認並解鎖"
        onReset={() => {
          setEngagedTaskId(pendingTaskId);
          setPendingTaskId(null);
        }}
      />
    );
  }
  // 簽名進行中一律顯示凍結的任務（忽略在途 refetch 回填的新 data），避免 POST 途中換人。
  const shown = frozenTask ?? data;
  if (!shown) {
    if (cart.isError) return <Standby message="客顯同步中斷，正在重新連線…" />;
    if (cart.data) {
      return <CartScreen cart={cart.data} streamConnected={streamConnected} />;
    }
    return <Standby />;
  }
  if (shown.status === "PENDING") {
    return (
      <PendingTaskScreen
        task={shown}
        message={ackError ?? "正在確認簽署畫面…"}
      />
    );
  }
  if (shown.status === "SIGNED") {
    return (
      <StaffGate
        variant="done"
        title="簽署已完成"
        message="請將裝置交回店員，等待完成結帳。"
        unlockLabel="重新確認狀態"
        onReset={() => void queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] })}
      />
    );
  }
  // key=task.id：任務換人即重新掛載，本地狀態（簽名/勾選/撥款）自然重置，
  // 不需 effect 手動清（避免沿用上一位客人的確認旗標）。
  return (
    <TaskScreen
      key={shown.id}
      task={shown}
      csrf={csrf}
      onSigningChange={onSigningChange}
      onComplete={() => {
        // 簽名是個資：完成後立即從客顯記憶體快取與同步 state 清除，
        // 交回店員畫面只保留不含內容的 handoff lock。
        queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
        setSyncedData(undefined);
        setAckError(null);
        setPendingTaskId(null);
        setHandoffLock(true); // 持久化交回鎖：重整也停在交回畫面，須店員解鎖
        setFrozenTask(null);
        setCompleted(true);
      }}
    />
  );
}

function CompletedSaleScreen({ cart }: { cart: KioskCart }) {
  return (
    <main className="kiosk-thanks">
      <div className="kiosk-thanks-inner">
        <div className="kiosk-thanks-check" aria-hidden>
          ✓
        </div>
        <h1 className="kiosk-thanks-title">交易已完成</h1>
        <p className="kiosk-standby-sub">
          本次金額 ${formatNtd(parseNtd(cart.snapshot.total) ?? 0)}
        </p>
        <p className="hint">謝謝光臨，畫面將自動清除。</p>
      </div>
    </main>
  );
}

function CartScreen({
  cart,
  streamConnected,
}: {
  cart: KioskCart;
  streamConnected: boolean;
}) {
  const { snapshot, changes } = cart;
  const changesByItem = new Map(
    changes
      .filter((change) => change.item_key !== "TOTAL")
      .map((change) => [change.item_key, change.type]),
  );
  return (
    <main className="kiosk-cart-shell">
      <header className="kiosk-cart-header">
        <div>
          <p className="kiosk-eyebrow">顧客購物明細</p>
          <h1>
            {cart.status === "PROCESSING"
              ? "付款處理中，請稍候"
              : cart.status === "PAYMENT_UNCERTAIN"
                ? "付款確認中，請勿重複付款"
                : "請核對本次購買內容"}
          </h1>
        </div>
        <span className={streamConnected ? "kiosk-live is-online" : "kiosk-live"}>
          <i aria-hidden />
          {streamConnected ? "即時同步" : "重新連線中"}
        </span>
      </header>

      {changes.length > 0 && (
        <div className="kiosk-cart-changes" aria-live="polite">
          {changes.map((change, index) => (
            <p
              key={`${cart.revision}:${change.type}:${change.item_key}:${index}`}
              className={`kiosk-cart-change is-${change.type.toLowerCase()}`}
            >
              <strong>{change.name}</strong>
              {change.type === "ADDED" && " 已加入"}
              {change.type === "REMOVED" && " 已移除"}
              {change.type === "DISCOUNT_CHANGED" && "，應付總額已更新"}
              {change.type === "QUANTITY_CHANGED" && (
                <>
                  {" "}
                  <span>
                    {change.from_qty} → {change.to_qty}
                  </span>
                </>
              )}
            </p>
          ))}
        </div>
      )}

      <section className="kiosk-cart-items" aria-label="商品明細">
        {snapshot.items.map((item) => (
          <article
            className={`kiosk-cart-item ${
              changesByItem.get(item.item_key) === "ADDED"
                ? "is-added"
                : changesByItem.get(item.item_key) === "QUANTITY_CHANGED"
                  ? "is-updated"
                  : ""
            }`}
            key={item.item_key}
          >
            <div>
              <h2>{item.name}</h2>
              <p>
                單價 ${formatNtd(parseNtd(item.unit_price) ?? 0)}
                {item.discount_amount !== "0" && (
                  <span> · 折扣 −${formatNtd(parseNtd(item.discount_amount) ?? 0)}</span>
                )}
              </p>
            </div>
            <span className="kiosk-cart-qty">× {item.qty}</span>
            <strong>${formatNtd(parseNtd(item.line_total) ?? 0)}</strong>
          </article>
        ))}
      </section>

      <footer className="kiosk-cart-total" data-testid="kiosk-total-bar">
        <div className="kiosk-cart-meta">
          {snapshot.member && (
            <p>
              <span>會員</span>
              <strong>{snapshot.member.display_name}</strong>
            </p>
          )}
          {snapshot.tenders.length > 0 && (
            <p>
              <span>付款方式</span>
              <strong>
                {snapshot.tenders.map((tender) => tenderLabel(tender.tender_type)).join("＋")}
              </strong>
            </p>
          )}
        </div>
        {snapshot.discount_total !== "0" && (
          <p className="kiosk-cart-discount">
            本次共折抵 ${formatNtd(parseNtd(snapshot.discount_total) ?? 0)}
          </p>
        )}
        <div className="kiosk-cart-grand-total">
          <span>應付總額</span>
          <strong>${formatNtd(parseNtd(snapshot.total) ?? 0)}</strong>
        </div>
      </footer>
    </main>
  );
}

function tenderLabel(tender: components["schemas"]["TenderType"]): string {
  switch (tender) {
    case "STORE_CREDIT":
      return "購物金";
    case "LINE_PAY":
      return "LINE Pay";
    case "TAIWAN_PAY":
      return "台灣 Pay";
    default:
      return "現金";
  }
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

function Standby({
  message = "請稍候，店員將為您加入商品。",
}: {
  message?: string;
}) {
  return (
    <main className="kiosk-standby">
      <div className="kiosk-standby-inner">
        <h1 className="kiosk-standby-title">露營二手</h1>
        <p className="kiosk-standby-sub">{message}</p>
        <div className="kiosk-standby-dot" aria-hidden />
      </div>
    </main>
  );
}

function PendingTaskScreen({
  task,
  message,
}: {
  task: KioskTask;
  message: string;
}) {
  return (
    <main className="kiosk-task">
      <header className="kiosk-task-header">
        <h1 className="kiosk-task-title">{taskHeading(task.kind)}</h1>
      </header>
      <section className="kiosk-task-body" aria-busy="true">
        <ContentSnapshot content={task.content} />
        {task.agreement_body !== null && (
          <div className="kiosk-agreement">
            <h2 className="kiosk-agreement-title">{task.agreement_title}</h2>
            <div className="kiosk-agreement-body">{task.agreement_body}</div>
          </div>
        )}
      </section>
      <footer className="kiosk-task-footer">
        <p className="hint" aria-live="polite">
          {message}
        </p>
      </footer>
    </main>
  );
}

// ── 任務畫面：切結書 + 明細 + 撥款 + 簽名 ────────────────────────────────
const PAYOUT_KINDS = new Set(["ACQUISITION_AFFIDAVIT"]);

function TaskScreen({
  task,
  csrf,
  onComplete,
  onSigningChange,
}: {
  task: KioskTask;
  csrf: string;
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

  function reportActivity(
    activity: "SIGNATURE_STARTED" | "SIGNATURE_INPUT" | "SIGNATURE_CLEARED" | "PAYOUT_SELECTED",
  ) {
    void kioskApi.POST("/api/v1/kiosk/tasks/{task_id}/activity", {
      params: { path: { task_id: task.id } },
      headers: { "X-CSRF-Token": csrf },
      body: { activity },
    });
  }

  useEffect(() => {
    reportActivity("SIGNATURE_STARTED");
    // 任務 id 變更會以 key 重新掛載；每張任務只送一次開始事件。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      const { response } = await kioskApi.POST("/api/v1/kiosk/tasks/{task_id}/sign", {
        params: { path: { task_id: task.id } },
        headers: { "X-CSRF-Token": csrf },
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
                onClick={() => {
                  setPayout("CASH");
                  reportActivity("PAYOUT_SELECTED");
                }}
              >
                <span className="kiosk-payout-label">現金</span>
                <span className="kiosk-payout-amount">{formatAmount(task.content.total)}</span>
              </button>
              <button
                type="button"
                className={payoutClass(payout === "STORE_CREDIT")}
                disabled={controlsLocked}
                onClick={() => {
                  setPayout("STORE_CREDIT");
                  reportActivity("PAYOUT_SELECTED");
                }}
              >
                <span className="kiosk-payout-label">購物金</span>
                {storeCreditPremium(task.content) ? (
                  <span className="kiosk-payout-amount">
                    {formatAmount(storeCreditPremium(task.content)?.amount)}
                    <span className="kiosk-payout-bonus">
                      多得 {formatAmount(storeCreditPremium(task.content)?.extra)}
                    </span>
                  </span>
                ) : (
                  <span className="kiosk-payout-amount">{formatAmount(task.content.total)}</span>
                )}
              </button>
            </div>
          </div>
        )}

        <div className="kiosk-signature">
          <h2 className="kiosk-section-title">簽名確認</h2>
          <SignatureCanvas
            ref={canvasRef}
            onInkChange={(ink) => {
              setHasInk(ink);
              reportActivity(ink ? "SIGNATURE_INPUT" : "SIGNATURE_CLEARED");
            }}
            locked={controlsLocked}
          />
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
  content_version: "內容版本",
  seller_name: "姓名",
  member_name: "會員",
  member: "會員",
  national_id_masked: "身分證字號",
  phone: "電話",
  address: "住址",
  total: "合計金額",
  deduct: "扣抵購物金",
  debit: "本次折抵",
  balance: "購物金餘額",
  balance_before: "目前購物金餘額",
  balance_after: "折抵後剩餘",
  sale_total: "本次消費合計",
  sale_ref: "銷售單號",
  purchased_at: "交易時間",
  discount_total: "折扣合計",
  store_credit_amount: "本次使用購物金",
  store_credit_balance_before: "扣抵前購物金餘額",
  store_credit_balance_after: "扣抵後購物金餘額",
  remaining_tenders: "剩餘付款",
  campaign_name: "優惠活動",
  qty: "數量",
  unit_price: "單價",
  original_unit_price: "原價",
  discount_amount: "折扣",
  line_total: "小計",
};

// 客人簽的是完整 JSON 快照，故此處**窮舉渲染**所有欄位、不靜默丟棄任何鍵
// （Codex K3 high：簽到沒看到的內容＝證據風險）。已知鍵給中文標籤與金額格式，
// 未知鍵照原樣列出；巢狀物件/陣列（items 以外）以可讀字串呈現。
function ContentSnapshot({ content }: { content: Record<string, unknown> }) {
  const itemsIsArray = Array.isArray(content.items);
  const items = itemsIsArray ? (content.items as unknown[]) : [];
  // 僅在 items 真的以陣列渲染時，才將它排除於一般欄位；若 items 非陣列（schema 漂移/
  // 錯誤生產者），仍以一般欄位 renderValue 列出，絕不靜默丟棄客人所簽內容（Codex K3 高）。
  // store_credit_premium 於撥款按鈕另外呈現，不列入通用明細。
  // （綁定用身分指紋已移至後端內部欄，不在 content，故此處不再需要遮蔽。）
  const hidden = new Set(["store_credit_premium"]);
  const rest = Object.entries(content).filter(
    ([key]) => !hidden.has(key) && (key !== "items" || !itemsIsArray),
  );

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
              const extra = Object.entries(item).filter(
                ([k]) => k !== "name" && k !== "amount" && k !== "line_total",
              );
              return (
                <tr key={i}>
                  <td>
                    {String(item.name ?? "—")}
                    {extra.length > 0 && (
                      <span className="kiosk-item-extra">
                        {extra
                          .map(
                            ([k, v]) =>
                              `${CONTENT_LABELS[k] ?? k}：${
                                isAmountKey(k) ? formatAmount(v) : renderValue(v)
                              }`,
                          )
                          .join("；")}
                      </span>
                    )}
                  </td>
                  <td className="kiosk-items-amount">
                    {formatAmount(item.line_total ?? item.amount)}
                  </td>
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
              <dd>
                {isAmountKey(key)
                  ? formatAmount(value)
                  : key === "purchased_at" && typeof value === "string"
                    ? formatTaipeiDateTime(value)
                    : renderContentValue(key, value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function isAmountKey(key: string): boolean {
  return [
    "total",
    "deduct",
    "debit",
    "balance",
    "balance_before",
    "balance_after",
    "sale_total",
    "discount_total",
    "store_credit_amount",
    "store_credit_balance_before",
    "store_credit_balance_after",
    "unit_price",
    "original_unit_price",
    "discount_amount",
    "line_total",
  ].includes(key);
}

function renderContentValue(key: string, value: unknown): string {
  if (
    key === "member" &&
    value !== null &&
    typeof value === "object" &&
    "display_name" in value
  ) {
    return String((value as { display_name: unknown }).display_name);
  }
  if (key === "remaining_tenders" && Array.isArray(value)) {
    return value
      .map((raw) => {
        if (raw === null || typeof raw !== "object") return renderValue(raw);
        const tender = raw as Record<string, unknown>;
        const type = tender.tender_type;
        const label =
          type === "LINE_PAY"
            ? "LINE Pay"
            : type === "TAIWAN_PAY"
              ? "台灣 Pay"
              : type === "STORE_CREDIT"
                ? "購物金"
                : "現金";
        return `${label} ${formatAmount(tender.amount)}`;
      })
      .join("＋");
  }
  return renderValue(value);
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

// 後端於 AFFIDAVIT 內容補的購物金溢價預覽（客人選購物金可多得幾%；使用者裁示）。
function storeCreditPremium(
  content: Record<string, unknown>,
): { amount: unknown; extra: unknown } | null {
  const p = content.store_credit_premium;
  if (p === null || typeof p !== "object") return null;
  const rec = p as Record<string, unknown>;
  if (rec.amount === undefined) return null;
  return { amount: rec.amount, extra: rec.extra };
}
