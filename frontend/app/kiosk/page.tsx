"use client";
// 顧客螢幕／手持簽署共用頁：裝置 cookie 登入與配對，SSE 通知後全量重讀權威購物車；
// 使用購物金或收購任務時，沿用下方不可變內容快照與簽名流程。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { API_BASE_URL, kioskApi } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { verifyStaffCredentials } from "@/lib/auth";
import { STORE_DISPLAY_NAME } from "@/lib/branding";
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

// 完成畫面自動清場秒數：簽署完成與成交完成共用同一套倒數 UX。
const AUTO_STANDBY_MS = 10_000;

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
// 舊版「交回鎖」的 key。交回鎖已移除（簽畢自動回待機），但既有平板的 localStorage 仍可能
// 留著它與當時的釘選；若不一併清除，升級後第一張任務會被當成「任務已更新」而要求店員帳密
// ——正是這次要消除的重複登入（Codex P1）。清除是安全的：留有交回鎖代表上一位已簽畢離場。
const LEGACY_HANDOFF_KEY = "lu-camp.kiosk-handoff";
function readEngagedTask(): number | null {
  if (typeof window === "undefined") return null;
  if (window.localStorage.getItem(LEGACY_HANDOFF_KEY) !== null) {
    window.localStorage.removeItem(LEGACY_HANDOFF_KEY);
    window.localStorage.removeItem(ENGAGED_KEY);
    return null;
  }
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
  // 避免重新整理時伺服器登入畫面與瀏覽器顧客螢幕畫面不一致。
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
      if (!data) throw new Error("無法讀取顧客螢幕裝置狀態");
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
  return (
    <KioskConsole
      csrf={csrf}
      terminalName={device.data.paired_terminal.name}
    />
  );
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
        <h1 className="kiosk-login-title">顧客螢幕設定</h1>
        <p className="kiosk-login-sub">以本店顧客螢幕帳號登入；登入後再與 POS 櫃檯配對。</p>
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
        <p>請在 POS 輸入這組配對碼。配對好之後，這個畫面會自動變成客人的購物車。</p>
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
function KioskConsole({
  csrf,
  terminalName,
}: {
  csrf: string;
  terminalName: string;
}) {
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
  // 簽署完成的感謝畫面（店主裁示：移除店員帳密交回鎖）：上一位客人整筆結束離場後才會叫
  // 下一位，故不需帳密交接；此畫面本身不含任何個資。倒數期間暫停輪詢，避免感謝畫面被
  // 下一張任務直接取代，到期後自動清場回待機（比照成交完成畫面）。
  const [signedAt, setSignedAt] = useState<number | null>(null);
  const completed = signedAt !== null;
  const [signedSeconds, setSignedSeconds] = useState(AUTO_STANDBY_MS / 1_000);
  // 曖昧簽署恢復（Codex K3 第七輪 high）：重掛時若持久化簽署鎖仍在（thrown 後未收斂又重整），
  // 進入店員解鎖恢復畫面、不輪詢——避免把下一位任務顯示給前一位客人。
  const [recovering, setRecovering] = useState(readSigningLock);
  // 簽名送出進行中亦暫停輪詢（Codex K3 high）：否則 POST 尚未回應期間，輪詢可能因
  // 店員重推而換掉 data、使 key=id 重掛出下一張任務，讓前一位客人看到他人內容。
  // 僅暫停 enabled 不夠——POST 前已在途的 refetch 仍可能回填快取；故簽名期間另以
  // frozenTask 凍結畫面上的任務、並 cancelQueries 中止在途請求（Codex K3 第五輪 high）。
  const [frozenTask, setFrozenTask] = useState<KioskTask | null>(null);
  // 顯示中的任務一經呈現即「釘住」其 id（Codex K3 第十輪 high）：店員於客人**尚未簽完**前
  // 取消並改推另一張任務時，不得自動把新任務換到客人面前（可能是下一位客人的內容/個資）——
  // 需店員確認解鎖後才採用。以 React 官方「prop 變更時調整 state」模式於 render 中同步（非
  // effect、非 ref-in-render），待機清除、首張認領、不同任務不採用（交由 render 顯示閘門）。
  // 任務一旦 SIGNED 即釋放釘選：該客人已簽畢離場，下一張任務直接顯示、不必店員解鎖。
  const [engagedTaskId, setEngagedTaskId] = useState<number | null>(readEngagedTask);
  // 任務被店員撤回後若立刻換成另一位顧客的任務，只保留新任務 id 作為交接閘門；
  // 新任務完整內容立刻自 Query cache 清除，待店員確認後才重新向後端讀取。
  const [pendingTaskId, setPendingTaskId] = useState<number | null>(null);
  const [syncedData, setSyncedData] = useState<KioskTask | null | undefined>(undefined);
  const [ackError, setAckError] = useState<string | null>(null);
  const acknowledging = useRef<number | null>(null);
  const [completionSeconds, setCompletionSeconds] = useState(10);
  const signing = frozenTask !== null;
  const paused = completed || signing || recovering || pendingTaskId !== null;

  useEffect(() => {
    if (cart.data?.status !== "COMPLETED") return;
    const completedAt = Date.parse(cart.data.updated_at);
    const remaining = Number.isFinite(completedAt)
      ? Math.max(0, completedAt + AUTO_STANDBY_MS - Date.now())
      : AUTO_STANDBY_MS;
    const updateCountdown = () => {
      const milliseconds = Number.isFinite(completedAt)
        ? Math.max(0, completedAt + AUTO_STANDBY_MS - Date.now())
        : AUTO_STANDBY_MS;
      setCompletionSeconds(Math.ceil(milliseconds / 1_000));
    };
    updateCountdown();
    const countdown = window.setInterval(updateCountdown, 1_000);
    const timer = window.setTimeout(() => {
      // 成交後舊簽署已 CONSUMED；完成畫面到期時一併清掉簽署鎖、任務釘選與
      // 本機快取，否則會從「交易已完成」退回簽署畫面而無法回待機。
      setSigningLock(false);
      writeEngagedTask(null);
      queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
      setSyncedData(undefined);
      setAckError(null);
      setPendingTaskId(null);
      setEngagedTaskId(null);
      setRecovering(false);
      setSignedAt(null);
      void queryClient.invalidateQueries({ queryKey: ["kiosk", "cart"] });
    }, remaining);
    return () => {
      window.clearInterval(countdown);
      window.clearTimeout(timer);
    };
  }, [cart.data?.status, cart.data?.updated_at, queryClient]);

  // 簽署完成 → 感謝畫面倒數 → 自動清場回待機並恢復輪詢（不需店員操作）。
  useEffect(() => {
    if (signedAt === null) return;
    const updateCountdown = () => {
      setSignedSeconds(
        Math.ceil(Math.max(0, signedAt + AUTO_STANDBY_MS - Date.now()) / 1_000),
      );
    };
    updateCountdown();
    const countdown = window.setInterval(updateCountdown, 1_000);
    const timer = window.setTimeout(
      () => {
        // 與原「店員解鎖」相同的清場：清任務釘選、待確認任務與本機快取後再恢復輪詢，
        // 避免恢復瞬間閃現上一位客人的舊任務。
        setEngagedTaskId(null);
        setPendingTaskId(null);
        setSyncedData(undefined);
        setAckError(null);
        queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
        setSignedAt(null);
      },
      Math.max(0, signedAt + AUTO_STANDBY_MS - Date.now()),
    );
    return () => {
      window.clearInterval(countdown);
      window.clearTimeout(timer);
    };
  }, [queryClient, signedAt]);

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
          setAckError("客人太久沒簽或已取消，正在重新載入…");
        }
        await queryClient.invalidateQueries({ queryKey: ["kiosk", "current"] });
      })
      .catch(() => setAckError("無法確認簽署畫面，正在重新連線…"))
      .finally(() => {
        acknowledging.current = null;
      });
  }, [csrf, data?.id, data?.status, paused, queryClient]);

  // 於 render 中同步釘選（React 官方模式；React Query 結構共享使 data 參考在內容不變時穩定）。
  // **釘選不因輪詢回 null 而清**（Codex K3 第十一輪 high）：否則「顯示 A → 店員取消 A
  // （current=null）→ 建立 B」的空窗會讓 B 被當成首張任務直接顯示、繞過閘門。
  // 只有兩條路徑會釋放釘選：店員確認解鎖，或該任務已 SIGNED（客人已簽畢、交易結束）。
  if (!paused && data !== syncedData) {
    const id = data?.id ?? null;
    const alreadySigned = data?.status === "SIGNED";
    if (id !== null && engagedTaskId === null) {
      setSyncedData(data);
      // 已簽畢的任務不再釘選：下一張任務可直接顯示，不需店員帳密交接。
      if (!alreadySigned) setEngagedTaskId(id);
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
      // 釘選中的任務已完成簽署 → 釋放釘選（客人已簽畢離場）。
      if (id !== null && id === engagedTaskId && alreadySigned) setEngagedTaskId(null);
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

  // 付款階段是購物車的權威狀態，必須蓋過簽署完成畫面與仍為 SIGNED 的任務：
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
    return <CompletedSaleScreen cart={cart.data} remainingSeconds={completionSeconds} />;
  }

  if (recovering) {
    return (
      <StaffGate
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
    return <SignedThanksScreen remainingSeconds={signedSeconds} />;
  }
  if (pendingTaskId !== null) {
    return (
      <StaffGate
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
    if (cart.isError) {
      return (
        <Standby
          message="顧客螢幕同步中斷，正在重新連線…"
          terminalName={terminalName}
        />
      );
    }
    if (cart.data) {
      return <CartScreen cart={cart.data} streamConnected={streamConnected} />;
    }
    return <Standby terminalName={terminalName} />;
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
    // 已簽畢但店員尚未完成後續作業（收購送出／結帳）：只顯示等待訊息，輪詢照常，
    // 任務被消化或換新任務時自動更新畫面。
    return <SignedThanksScreen remainingSeconds={null} />;
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
        // 簽名是個資：完成後立即從顧客螢幕記憶體快取與同步 state 清除；
        // 感謝畫面只有不含內容的倒數，到期自動回待機。
        queryClient.removeQueries({ queryKey: ["kiosk", "current"] });
        setSyncedData(undefined);
        setAckError(null);
        setPendingTaskId(null);
        // 客人已簽畢＝不再「進行中」：釋放釘選（含 localStorage），倒數結束或中途重整
        // 都能直接接續下一張任務，不必店員輸入帳密。
        setEngagedTaskId(null);
        setFrozenTask(null);
        setSignedAt(Date.now());
      }}
    />
  );
}

function CompletedSaleScreen({
  cart,
  remainingSeconds,
}: {
  cart: KioskCart;
  remainingSeconds: number;
}) {
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
        <p className="hint" role="status">
          謝謝光臨，{remainingSeconds} 秒後自動清除。
        </p>
      </div>
    </main>
  );
}

// 簽署完成的顧客畫面（店主裁示取代店員交回鎖）：倒數中自動回待機；若店員後續作業尚未
// 完成（任務仍為 SIGNED）則不倒數，僅告知稍候，畫面不含任何個資。
function SignedThanksScreen({ remainingSeconds }: { remainingSeconds: number | null }) {
  return (
    <main className="kiosk-thanks">
      <div className="kiosk-thanks-inner">
        <div className="kiosk-thanks-check" aria-hidden>
          ✓
        </div>
        <h1 className="kiosk-thanks-title">已完成簽署</h1>
        <p className="kiosk-standby-sub">感謝您</p>
        <p className="hint" role="status">
          {remainingSeconds === null
            ? "請稍候，店員將完成後續作業。"
            : `${remainingSeconds} 秒後自動回到待機畫面。`}
        </p>
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
  const itemListRef = useRef<HTMLElement>(null);
  const [scrollState, setScrollState] = useState({
    hasAbove: false,
    hasBelow: false,
  });
  const updateScrollState = useCallback(() => {
    const list = itemListRef.current;
    if (!list) return;
    const next = {
      hasAbove: list.scrollTop > 4,
      hasBelow: list.scrollTop + list.clientHeight < list.scrollHeight - 4,
    };
    setScrollState((current) =>
      current.hasAbove === next.hasAbove && current.hasBelow === next.hasBelow
        ? current
        : next,
    );
  }, []);
  useEffect(() => {
    updateScrollState();
    const frame = window.requestAnimationFrame(updateScrollState);
    window.addEventListener("resize", updateScrollState);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updateScrollState);
    if (itemListRef.current) observer?.observe(itemListRef.current);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateScrollState);
      observer?.disconnect();
    };
  }, [snapshot.items.length, updateScrollState]);
  const changesByItem = new Map(
    changes
      .filter((change) => change.item_key !== "TOTAL")
      .map((change) => [change.item_key, change.type]),
  );
  const visibleChanges = changes.filter((change) => change.type !== "ADDED");
  return (
    <main className="kiosk-cart-shell">
      <header className="kiosk-cart-header">
        <div>
          <p className="kiosk-eyebrow">顧客購物明細</p>
          <h1>
            {cart.status === "PROCESSING" && (
              <span
                className="kiosk-payment-spinner"
                role="status"
                aria-label="付款處理中"
              />
            )}
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

      {visibleChanges.length > 0 && (
        <div className="kiosk-cart-changes" aria-live="polite">
          {visibleChanges.map((change, index) => (
            <p
              key={`${cart.revision}:${change.type}:${change.item_key}:${index}`}
              className={`kiosk-cart-change is-${change.type.toLowerCase()}`}
            >
              <strong>{change.name}</strong>
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

      <section
        ref={itemListRef}
        className={`kiosk-cart-items${scrollState.hasAbove ? " has-above" : ""}${
          scrollState.hasAbove || scrollState.hasBelow ? " has-scroll-hint" : ""
        }`}
        aria-label="商品明細"
        onScroll={updateScrollState}
      >
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
              <h2>
                {item.name}
                {item.line_kind === "GIFT" && (
                  <span className="kiosk-cart-gift">贈品</span>
                )}
              </h2>
              <p className="kiosk-cart-price-detail">
                {item.line_kind === "GIFT" ? (
                  <>
                    <span className="kiosk-cart-original-price">
                      原價 ${formatNtd(parseNtd(item.original_unit_price ?? "0") ?? 0)}
                    </span>
                    <span>本次贈送，不收費</span>
                  </>
                ) : item.original_unit_price !== null ? (
                  <>
                    <span className="kiosk-cart-original-price">
                      原價 ${formatNtd(parseNtd(item.original_unit_price) ?? 0)}
                    </span>
                    <span>優惠價 ${formatNtd(parseNtd(item.unit_price) ?? 0)}</span>
                    <span className="kiosk-cart-line-discount">
                      折扣 ${formatNtd(parseNtd(item.discount_amount) ?? 0)}
                    </span>
                  </>
                ) : (
                  <span>單價 ${formatNtd(parseNtd(item.unit_price) ?? 0)}</span>
                )}
                {/* 臨時折扣：客顯是客人**核對金額**的地方，只顯示活動折扣的話，
                    客人會看到列出 500 卻收 400，卻沒有任何說明。 */}
                {parseNtd(item.manual_discount_amount) !== 0 && (
                  <span className="kiosk-cart-line-discount">
                    店家折扣 −${formatNtd(parseNtd(item.manual_discount_amount) ?? 0)}
                  </span>
                )}
              </p>
            </div>
            <span className="kiosk-cart-qty">× {item.qty}</span>
            {/* 小計認**實付**：line_total 是活動折後的牌價小計，不含臨時折扣。 */}
            <strong>${formatNtd(parseNtd(item.net_amount) ?? 0)}</strong>
          </article>
        ))}
      </section>
      {(scrollState.hasAbove || scrollState.hasBelow) && (
        <p className="kiosk-cart-scroll-hint" aria-live="polite">
          {scrollState.hasBelow
            ? scrollState.hasAbove
              ? `共 ${snapshot.items.length} 個品項 · 可上下滑動 ↕`
              : `共 ${snapshot.items.length} 個品項 · 向下滑查看更多 ↓`
            : `已顯示全部 ${snapshot.items.length} 個品項 · 向上滑可返回 ↑`}
        </p>
      )}

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
        {(parseNtd(snapshot.discount_total) ?? 0) +
          (parseNtd(snapshot.manual_discount_total) ?? 0) >
          0 && (
          <p className="kiosk-cart-discount">
            本次共折扣 $
            {formatNtd(
              (parseNtd(snapshot.discount_total) ?? 0) +
                (parseNtd(snapshot.manual_discount_total) ?? 0),
            )}
          </p>
        )}
        {(parseNtd(snapshot.gift_retail_value) ?? 0) > 0 && (
          <p className="kiosk-cart-discount">
            贈品價值 ${formatNtd(parseNtd(snapshot.gift_retail_value) ?? 0)}（不計入應付）
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

// 店員帳密解鎖畫面（Codex K3 high）：需人工判斷的恢復路徑（曖昧簽署、店員中途換任務）
// 才走這裡，須現場店務員帳密授權，避免客人自行點按解鎖看到下一位客人內容。
// 驗證不持久化 token（裝置身分仍為 KIOSK）。
function StaffGate({
  title,
  message,
  unlockLabel,
  onReset,
}: {
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
        <div className="kiosk-thanks-check kiosk-thanks-check--warn" aria-hidden>
          !
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
  terminalName,
}: {
  message?: string;
  terminalName?: string;
}) {
  return (
    <main className="kiosk-standby">
      {terminalName && (
        <p className="kiosk-terminal-label">櫃檯 · {terminalName}</p>
      )}
      <div className="kiosk-standby-inner">
        <h1 className="kiosk-standby-title">{STORE_DISPLAY_NAME}</h1>
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
        <ContentSnapshot kind={task.kind} content={task.content} />
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
        <ContentSnapshot kind={task.kind} content={task.content} />

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
            <h2 className="kiosk-section-title">
              請選擇收款方式
              <span className="kiosk-required-badge">必選</span>
            </h2>
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
    case "RETURN_INVOICE_CONSENT":
      return "退貨與發票處置同意";
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
  invoice_no: "原發票號碼",
  refund_total: "退款金額",
  invoice_action_label: "發票處置方式",
  consent_note: "同意內容",
};

type ContentEntry = [string, unknown];
type ContentFieldGroup = { title: string | null; entries: ContentEntry[] };

const STORE_CREDIT_FIELD_ORDER = [
  "total",
  "sale_total",
  "member",
  "campaign_name",
  "discount_total",
  "store_credit_balance_before",
  "balance_before",
  "store_credit_amount",
  "debit",
  "store_credit_balance_after",
  "balance_after",
  "remaining_tenders",
];
const AFFIDAVIT_IDENTITY_FIELD_ORDER = [
  "seller_name",
  "national_id_masked",
  "phone",
  "address",
];
const AFFIDAVIT_TRANSACTION_FIELD_ORDER = ["total", "lot"];
const ACK_FIELD_ORDER = ["sale_ref", "purchased_at", "total"];
const RETURN_CONSENT_FIELD_ORDER = [
  "sale_ref",
  "purchased_at",
  "invoice_no",
  "refund_total",
  "invoice_action_label",
  "consent_note",
];
const ITEM_EXTRA_ORDER = [
  "qty",
  "original_unit_price",
  "unit_price",
  "discount_amount",
];

// 客人簽的是完整 JSON 快照，故此處**窮舉渲染**所有欄位、不靜默丟棄任何鍵
// （Codex K3 high：簽到沒看到的內容＝證據風險）。已知鍵給中文標籤與金額格式，
// 未知鍵照原樣列出；巢狀物件/陣列（items 以外）以可讀字串呈現。
function ContentSnapshot({
  kind,
  content,
}: {
  kind: string;
  content: Record<string, unknown>;
}) {
  const itemsIsArray = Array.isArray(content.items);
  const items = itemsIsArray ? (content.items as unknown[]) : [];
  // 僅在 items 真的以陣列渲染時，才將它排除於一般欄位；若 items 非陣列（schema 漂移/
  // 錯誤生產者），仍以一般欄位 renderValue 列出，絕不靜默丟棄客人所簽內容（Codex K3 高）。
  // store_credit_premium 於撥款按鈕另外呈現，不列入通用明細。
  // （綁定用身分指紋已移至後端內部欄，不在 content，故此處不再需要遮蔽。）
  // 機器比對用的欄位（退貨同意）：都與上方已呈現的內容同一份資料——return_lines 對應品項
  // 明細、invoice_id 對應原發票號碼、invoice_action 對應處置方式的中文說明。客人看到的
  // 內容不因隱藏而少一分；沿 store_credit_premium 的既有做法排除。
  const hidden = new Set([
    "store_credit_premium",
    "content_version",
    "return_lines",
    "invoice_id",
    "invoice_action",
  ]);
  const rest = Object.entries(content).filter(
    ([key]) => !hidden.has(key) && (key !== "items" || !itemsIsArray),
  );
  const groups = contentFieldGroups(kind, rest);
  const documentVersion =
    "content_version" in content ? friendlyDocumentVersion(content.content_version) : null;
  const itemTable =
    items.length > 0 ? (
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
            // 主要金額認**實付**：購物金簽署的 items 同時有 line_total 與 net_amount，
            // 拿 line_total 當金額會讓客人看到「商品 500、整單 400」卻沒有說明。
            // 退貨同意的 line_total 本身就是退款額（無 net_amount），沿用即可。
            const primaryAmount = item.net_amount ?? item.line_total ?? item.amount;
            const isGift = item.line_kind === "GIFT";
            const manualDiscount = item.manual_discount_amount;
            const extra = orderEntries(
              Object.entries(item).filter(
                ([k]) =>
                  ![
                    "name",
                    "amount",
                    "line_total",
                    "net_amount",
                    "line_kind",
                    "manual_discount_amount",
                  ].includes(k),
              ),
              ITEM_EXTRA_ORDER,
            );
            return (
              <tr key={i}>
                <td>
                  {String(item.name ?? "—")}
                  {isGift && <span className="kiosk-cart-gift">贈品</span>}
                  {manualDiscount != null && String(manualDiscount) !== "0" && (
                    <span className="kiosk-item-extra">
                      店家折扣 −{formatAmount(manualDiscount)}
                    </span>
                  )}
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
                <td className="kiosk-items-amount">{formatAmount(primaryAmount)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    ) : null;

  return (
    <div className="kiosk-snapshot">
      {kind !== "ACQUISITION_AFFIDAVIT" && itemTable}
      {groups.map((group, index) => (
        <section className="kiosk-field-group" key={group.title ?? `fields-${index}`}>
          {group.title && <h2 className="kiosk-field-group-title">{group.title}</h2>}
          {kind === "ACQUISITION_AFFIDAVIT" &&
            group.title === "收購資料" &&
            itemTable}
          <dl className="kiosk-fields">
            {group.entries.map(([key, value]) => (
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
        </section>
      ))}
      {kind === "ACQUISITION_AFFIDAVIT" &&
        itemTable &&
        !groups.some((group) => group.title === "收購資料") && (
          <section className="kiosk-field-group">
            <h2 className="kiosk-field-group-title">收購資料</h2>
            {itemTable}
          </section>
        )}
      {documentVersion && <p className="kiosk-document-version">{documentVersion}</p>}
    </div>
  );
}

function contentFieldGroups(kind: string, entries: ContentEntry[]): ContentFieldGroup[] {
  if (kind === "ACQUISITION_AFFIDAVIT") {
    const identityKeys = new Set(AFFIDAVIT_IDENTITY_FIELD_ORDER);
    const transactionKeys = new Set(AFFIDAVIT_TRANSACTION_FIELD_ORDER);
    const identity = orderEntries(
      entries.filter(([key]) => identityKeys.has(key)),
      AFFIDAVIT_IDENTITY_FIELD_ORDER,
    );
    const transaction = orderEntries(
      entries.filter(([key]) => transactionKeys.has(key)),
      AFFIDAVIT_TRANSACTION_FIELD_ORDER,
    );
    const other = entries.filter(
      ([key]) => !identityKeys.has(key) && !transactionKeys.has(key),
    );
    return [
      { title: "賣方資料", entries: identity },
      { title: "收購資料", entries: transaction },
      { title: "其他內容", entries: other },
    ].filter((group) => group.entries.length > 0);
  }
  const order =
    kind === "STORE_CREDIT_USE"
      ? STORE_CREDIT_FIELD_ORDER
      : kind === "RETURN_INVOICE_CONSENT"
        ? RETURN_CONSENT_FIELD_ORDER
        : ACK_FIELD_ORDER;
  return [{ title: null, entries: orderEntries(entries, order) }];
}

function orderEntries(entries: ContentEntry[], order: string[]): ContentEntry[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort(
      (left, right) =>
        (rank.get(left.entry[0]) ?? order.length) -
          (rank.get(right.entry[0]) ?? order.length) ||
        left.index - right.index,
    )
    .map(({ entry }) => entry);
}

function friendlyDocumentVersion(value: unknown): string {
  if (typeof value === "string") {
    const version = value.match(/(?:^|-)v(\d+)$/i)?.[1];
    if (version) return `文件版本 v${version}`;
  }
  return `文件版本 ${renderValue(value)}`;
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
    "refund_total",
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
