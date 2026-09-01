// @vitest-environment jsdom
// 建檔不再問角色：每個人都是會員，賣過東西的人由系統自動標記（店主裁示 2026-09-01）。
//
// 店員判斷「這個人算不算賣方」對他毫無意義，而判斷錯了會讓收購被擋、或讓身分證字號的
// 登記要求被繞過。系統知道誰賣了東西，就該由系統標。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import ContactsPage from "@/app/(authed)/contacts/page";
import { rolesLabel } from "@/features/member/labels";

let posted: Record<string, unknown> | null = null;

function stubFetch() {
  posted = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (url.includes("/contacts") && method === "POST") {
        const raw = input instanceof Request ? await input.text() : String(init?.body ?? "{}");
        posted = JSON.parse(raw);
        return new Response(JSON.stringify({ id: 1, name: "王小明", roles: ["MEMBER"] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
}

function renderPage(node: ReactNode = <ContactsPage />) {
  render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

beforeEach(() => stubFetch());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("建檔不問角色", () => {
  it("畫面上沒有角色勾選框", () => {
    renderPage();

    expect(screen.queryByText("角色")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /寄售/ })).toBeNull();
  });

  it("身分證字號的說明不再提「寄售」——只有收購需要", () => {
    renderPage();

    // 頁面上有多處提到身分證字號（查找按鈕、欄位標籤、說明），逐一檢查而非只取第一個。
    const mentions = screen.getAllByText(/身分證字號/);
    expect(mentions.length).toBeGreaterThan(0);
    for (const el of mentions) expect(el.textContent).not.toContain("寄售");
  });

  it("只填姓名電話就能建檔，送出的角色是會員", async () => {
    renderPage();

    // 頁面同時有「查找」的姓名或電話欄，範圍限定在建檔表單內。
    const form = screen.getByRole("button", { name: "建檔" }).closest("form")!;
    const within = (label: string) =>
      Array.from(form.querySelectorAll("label")).find((l) =>
        l.textContent?.includes(label),
      )!.querySelector("input")!;

    await userEvent.type(within("姓名"), "王小明");
    await userEvent.type(within("電話"), "0912345678");
    await userEvent.click(screen.getByRole("button", { name: "建檔" }));

    expect(posted).not.toBeNull();
    expect(posted!.roles).toEqual(["MEMBER"]);
    expect(posted!.national_id).toBeNull();
  });
});

describe("角色顯示", () => {
  it("寄售人已併入賣方，不再有獨立的中文名", () => {
    // 舊資料若殘留 CONSIGNOR，翻譯表查不到會原樣顯示英文——那是刻意的（不吞未知值），
    // 但 migration 之後不該再有這個值。
    expect(rolesLabel(["MEMBER", "SELLER"])).toBe("會員、賣方");
    expect(rolesLabel(["MEMBER"])).toBe("會員");
  });
});
