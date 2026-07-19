// @vitest-environment jsdom
// /backup 備份儀表板測試：MANAGER 見健康度/設定/清單、金鑰保管提醒、手動觸發（POST）、
// 未設定回 503 顯示提示、非 MANAGER（health 403）顯示權限不足。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import BackupPage from "@/app/(authed)/backup/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

const HEALTH = {
  enabled: true,
  interval_hours: 24,
  retention: 30,
  offpeak_hour: 21,
  last_success_at: "2026-07-18T13:00:00Z",
  last_success_age_hours: 5.0,
  due_now: false,
  running: false,
};

const RUNS = [
  {
    id: 2,
    trigger: "SCHEDULED",
    status: "SUCCEEDED",
    started_at: "2026-07-18T13:00:00Z",
    finished_at: "2026-07-18T13:00:20Z",
    db_name: "lucamp",
    file_name: "lucamp_x.dump.enc",
    r2_key: "backups/lucamp_x.dump.enc",
    size_bytes: 3_145_728,
    sha256: "abcdef0123456789",
    last_error: null,
    actor_user_id: null,
  },
];

type FetchRoute = (url: string, init?: RequestInit) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const resp = route(url, { method } as RequestInit);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<BackupPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/backup", () => {
  it("MANAGER：顯示金鑰保管提醒、健康度、備份設定與紀錄", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/backup/health")) return json(HEALTH);
      if (url.includes("/backup/runs")) return json(RUNS);
      return null;
    });
    renderPage();
    expect(await screen.findByText(/兩組金鑰缺一即廢/)).toBeDefined();
    expect(screen.getByText("備份健康度")).toBeDefined();
    expect(screen.getByText("備份設定")).toBeDefined();
    // 紀錄表：成功狀態、大小格式化為 MB
    expect(screen.getByText("成功")).toBeDefined();
    expect(screen.getByText("3.00 MB")).toBeDefined();
  });

  it("非 MANAGER（health 403）：顯示需管理者權限、不渲染設定表單", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/backup/health")) return json({ detail: "權限不足" }, 403);
      if (url.includes("/backup/runs")) return json([]);
      return null;
    });
    renderPage();
    expect(await screen.findByText("需管理者權限")).toBeDefined();
    expect(screen.queryByText("備份設定")).toBeNull();
  });

  it("立即備份：POST /backup/runs（202 回 RUNNING）後觸發重新整理", async () => {
    loginAs("MANAGER");
    let posted = false;
    stubFetch((url, init) => {
      if (url.includes("/backup/runs") && init?.method === "POST") {
        posted = true;
        return json({ ...RUNS[0], id: 3, trigger: "MANUAL", status: "RUNNING" }, 202);
      }
      if (url.includes("/backup/health")) return json(HEALTH);
      if (url.includes("/backup/runs")) return json(RUNS);
      return null;
    });
    renderPage();
    const btn = await screen.findByRole("button", { name: "立即備份" });
    await userEvent.click(btn);
    await waitFor(() => expect(posted).toBe(true));
  });

  it("未設定（POST 回 503）：顯示尚未設定提示，不誤報成功", async () => {
    loginAs("MANAGER");
    stubFetch((url, init) => {
      if (url.includes("/backup/runs") && init?.method === "POST") {
        return json({ detail: "備份未設定（R2 憑證未提供）" }, 503);
      }
      if (url.includes("/backup/health")) return json(HEALTH);
      if (url.includes("/backup/runs")) return json(RUNS);
      return null;
    });
    renderPage();
    const btn = await screen.findByRole("button", { name: "立即備份" });
    await userEvent.click(btn);
    expect(await screen.findByText(/備份未設定/)).toBeDefined();
  });
});
