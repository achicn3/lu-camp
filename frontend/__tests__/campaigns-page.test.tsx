// @vitest-environment jsdom
// /campaigns 門市活動管理頁測試：清單渲染、建立表單、狀態操作、權限檢查、錯誤顯示。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import CampaignsPage from "@/app/(authed)/campaigns/page";
import { clearToken, setToken } from "@/lib/token";

// -- Fixture data --

const CAMPAIGN_DRAFT = {
  id: 1,
  store_id: 1,
  name: "開幕九折",
  discount_pct: 10,
  starts_at: "2026-06-20T00:00:00Z",
  ends_at: "2026-06-30T23:59:59Z",
  status: "DRAFT" as const,
  applies_owned_serialized: true,
  applies_owned_bulk: true,
  applies_catalog: false,
  applies_consignment: false,
  created_by: 1,
  created_at: "2026-06-19T10:00:00Z",
  updated_at: "2026-06-19T10:00:00Z",
};

const CAMPAIGN_ACTIVE = {
  ...CAMPAIGN_DRAFT,
  id: 2,
  name: "週年慶八折",
  discount_pct: 20,
  status: "ACTIVE" as const,
};

// -- Helpers --

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
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
  return render(<CampaignsPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearToken();
});

describe("CampaignsPage", () => {
  it("renders campaign list with name, discount, status, scope", async () => {
    loginAs("MANAGER");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/campaigns")) return json([CAMPAIGN_DRAFT, CAMPAIGN_ACTIVE]);
        throw new Error(`unmatched: ${url}`);
      }),
    );
    renderPage();

    expect(await screen.findByText("開幕九折")).toBeTruthy();
    expect(screen.getByText("週年慶八折")).toBeTruthy();
    // Discount display: 10% off = 9 折
    expect(screen.getByText("9 折")).toBeTruthy();
    // 20% off = 8 折
    expect(screen.getByText("8 折")).toBeTruthy();
    // Status labels in table (filter dropdown also has them, so use getAllByText)
    expect(screen.getAllByText("草稿").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("生效中").length).toBeGreaterThanOrEqual(1);
  });

  it("shows permission denied for non-MANAGER (403)", async () => {
    loginAs("CLERK");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "權限不足" }, 403)),
    );
    renderPage();
    expect(await screen.findByText("需管理者權限")).toBeTruthy();
  });

  it("shows friendly error on 409 conflict", async () => {
    loginAs("MANAGER");
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        const req = input instanceof Request ? input : undefined;
        // First call: list campaigns (GET)
        if (url.includes("/campaigns") && (!req || req.method === "GET")) {
          callCount++;
          if (callCount <= 1) return json([CAMPAIGN_DRAFT]);
          return json([CAMPAIGN_DRAFT]);
        }
        // activate call -> 409
        if (url.includes("/activate") && req?.method === "POST") {
          return json({ detail: "同店已有生效中活動" }, 409);
        }
        throw new Error(`unmatched: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("開幕九折");

    // Click the activate button
    const activateBtn = screen.getByRole("button", { name: "啟用" });
    await userEvent.click(activateBtn);

    expect(await screen.findByText("同店已有生效中活動")).toBeTruthy();
  });

  it("draft campaign shows activate and cancel buttons; active shows end and cancel", async () => {
    loginAs("MANAGER");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/campaigns")) return json([CAMPAIGN_DRAFT, CAMPAIGN_ACTIVE]);
        throw new Error(`unmatched: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("開幕九折");

    // Draft row: activate + cancel
    const activateButtons = screen.getAllByRole("button", { name: "啟用" });
    expect(activateButtons.length).toBe(1);

    const endButtons = screen.getAllByRole("button", { name: "結束" });
    expect(endButtons.length).toBe(1);

    const cancelButtons = screen.getAllByRole("button", { name: "作廢" });
    expect(cancelButtons.length).toBe(2); // both draft and active can be cancelled
  });

  it("create form submits and refreshes list", async () => {
    loginAs("MANAGER");
    const createdCampaign = { ...CAMPAIGN_DRAFT, id: 3, name: "新活動" };
    let postCalled = false;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        const req = input instanceof Request ? input : undefined;
        if (url.includes("/campaigns") && req?.method === "POST" && !url.includes("/activate") && !url.includes("/end") && !url.includes("/cancel")) {
          postCalled = true;
          return json(createdCampaign, 201);
        }
        if (url.includes("/campaigns") && (!req || req.method === "GET")) {
          return json(postCalled ? [createdCampaign] : []);
        }
        throw new Error(`unmatched: ${url}`);
      }),
    );
    renderPage();

    // Wait for initial load (empty list)
    await screen.findByText("尚無活動");

    // Fill in the form
    const nameInput = screen.getByLabelText("活動名稱");
    await userEvent.type(nameInput, "新活動");

    const discountInput = screen.getByLabelText("折扣 %（1-99）");
    await userEvent.type(discountInput, "10");

    const startInput = screen.getByLabelText("開始時間");
    await userEvent.type(startInput, "2026-06-20T00:00");

    const endInput = screen.getByLabelText("結束時間");
    await userEvent.type(endInput, "2026-06-30T23:59");

    // Submit
    const submitBtn = screen.getByRole("button", { name: "建立活動" });
    await userEvent.click(submitBtn);

    await waitFor(() => {
      expect(postCalled).toBe(true);
    });
  });
});
