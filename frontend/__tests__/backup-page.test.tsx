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

const RESTORES = [
  {
    id: 9,
    status: "VERIFIED",
    source_r2_key: "backups/lucamp_x.dump.enc",
    restore_db_name: "lucamp_restore_20260719_040000",
    started_at: "2026-07-19T04:00:00Z",
    finished_at: "2026-07-19T04:00:30Z",
    verifications: {
      all_ok: true,
      checks: [
        { name: "alembic_head", ok: true, detail: "head 相符" },
        { name: "table_counts", ok: true, detail: "sales=5313" },
        { name: "signature_bytea", ok: true, detail: "抽驗 5 筆" },
        { name: "backend_usable", ok: true, detail: "SELECT 1 ok" },
      ],
    },
    last_error: null,
    actor_user_id: 1,
  },
];

type FetchRoute = (url: string, init?: RequestInit) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const resp = route(url, { method, body } as RequestInit);
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
  it("MANAGER：顯示金鑰保管提醒、健康度、備份設定與紀錄、還原卡與四驗", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/backup/health")) return json(HEALTH);
      if (url.includes("/backup/restores")) return json(RESTORES);
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
    // 還原卡＋還原紀錄的四驗通過與各檢查項
    expect(screen.getByText("還原（災難復原）")).toBeDefined();
    expect(await screen.findByText("四驗通過")).toBeDefined();
    expect(screen.getAllByText(/lucamp_restore_20260719_040000/).length).toBeGreaterThan(0);
    expect(screen.getByText(/alembic_head/)).toBeDefined();
  });

  it("還原選單只列最新 retention 份（不列已被修剪刪除的舊備份）", async () => {
    loginAs("MANAGER");
    const manyRuns = [3, 2, 1].map((id) => ({
      ...RUNS[0],
      id,
      file_name: `lucamp_${id}.dump.enc`,
      r2_key: `backups/lucamp_${id}.dump.enc`,
    }));
    stubFetch((url) => {
      if (url.includes("/backup/health")) return json({ ...HEALTH, retention: 2 });
      if (url.includes("/backup/restores")) return json([]);
      if (url.includes("/backup/runs")) return json(manyRuns);
      return null;
    });
    renderPage();
    await screen.findByText("還原（災難復原）");
    // retention=2 → 選單只有 2 個備份選項（＋「請選擇」佔位）＝3 個 option；最舊的 lucamp_1 不列
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(screen.queryByText(/lucamp_1\.dump\.enc/)).toBeNull();
    expect(screen.getByText(/lucamp_3\.dump\.enc/)).toBeDefined();
  });

  it("非 MANAGER（health 403）：顯示需管理者權限、不渲染設定表單", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/backup/health")) return json({ detail: "權限不足" }, 403);
      if (url.includes("/backup/restores")) return json([]);
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
      if (url.includes("/backup/restores")) return json([]);
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
      if (url.includes("/backup/restores")) return json([]);
      if (url.includes("/backup/runs")) return json(RUNS);
      return null;
    });
    renderPage();
    const btn = await screen.findByRole("button", { name: "立即備份" });
    await userEvent.click(btn);
    expect(await screen.findByText(/備份未設定/)).toBeDefined();
  });

  it("還原卡控：需選備份→輸入檔名＋勾選才可觸發，成功 POST /backup/restore", async () => {
    loginAs("MANAGER");
    let restoreBody: Record<string, unknown> | null = null;
    stubFetch((url, init) => {
      if (url.includes("/backup/restore") && !url.includes("/backup/restores") && init?.method === "POST") {
        restoreBody = JSON.parse(String((init as { body?: string }).body ?? "{}"));
        return json(
          {
            id: 11,
            status: "RUNNING",
            source_r2_key: "backups/lucamp_x.dump.enc",
            restore_db_name: "lucamp_restore_new",
            started_at: "2026-07-19T05:00:00Z",
            finished_at: null,
            verifications: null,
            last_error: null,
            actor_user_id: 1,
          },
          202,
        );
      }
      if (url.includes("/backup/health")) return json(HEALTH);
      if (url.includes("/backup/restores")) return json([]);
      if (url.includes("/backup/runs")) return json(RUNS);
      return null;
    });
    renderPage();
    // 選擇要還原的備份
    const select = await screen.findByRole("combobox");
    await userEvent.selectOptions(select, "backups/lucamp_x.dump.enc");
    await userEvent.click(screen.getByRole("button", { name: /還原此備份到驗證庫/ }));
    // 確認按鈕在「輸入正確檔名＋勾選」前為 disabled
    const confirmBtn = screen.getByRole("button", { name: "確認還原到驗證庫" });
    expect((confirmBtn as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByRole("textbox"), "lucamp_x.dump.enc");
    await userEvent.click(screen.getByRole("checkbox", { name: "知情同意" }));
    expect((confirmBtn as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(confirmBtn);
    await waitFor(() => expect(restoreBody).not.toBeNull());
    expect(restoreBody).toMatchObject({
      source_r2_key: "backups/lucamp_x.dump.enc",
      confirm_text: "lucamp_x.dump.enc",
      acknowledge: true,
    });
  });
});
