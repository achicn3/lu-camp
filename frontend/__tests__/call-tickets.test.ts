import { describe, expect, it } from "vitest";

import {
  type CallTicket,
  isSafeExternalLink,
  ticketLabel,
  todayInTaipei,
} from "@/features/call-tickets/callTickets";

function ticket(overrides: Partial<CallTicket> = {}): CallTicket {
  return {
    id: 1,
    store_id: 1,
    ticket_date: "2026-08-19",
    ticket_no: 3,
    name: "王先生",
    link: null,
    note: null,
    status: "WAITING",
    created_at: "2026-08-19T02:00:00Z",
    completed_at: null,
    ...overrides,
  } as CallTicket;
}

describe("isSafeExternalLink（docs/38）", () => {
  it("放行 http 與 https", () => {
    expect(isSafeExternalLink("https://example.com/f/1")).toBe(true);
    expect(isSafeExternalLink("http://example.com")).toBe(true);
  });

  it("**擋下會在店員瀏覽器上執行或讀本機檔案的協定**", () => {
    for (const bad of ["javascript:alert(1)", "data:text/html,<script>x</script>", "file:///etc/passwd"]) {
      expect(isSafeExternalLink(bad)).toBe(false);
    }
  });

  it("格式錯誤或相對路徑不當成可點的外部連結", () => {
    expect(isSafeExternalLink("不是網址")).toBe(false);
    expect(isSafeExternalLink("/local/path")).toBe(false);
  });
});

describe("ticketLabel（docs/38）", () => {
  it("今日的只顯示號碼", () => {
    expect(ticketLabel(ticket(), "2026-08-19")).toBe("#3");
  });

  it("**跨日未完成的要標日期**——不標會與今日的號碼混淆", () => {
    expect(ticketLabel(ticket({ ticket_date: "2026-08-18", ticket_no: 7 }), "2026-08-19")).toBe(
      "8/18 #7",
    );
  });

  it("月份與日期不補零（8/1 而非 08/01）", () => {
    expect(ticketLabel(ticket({ ticket_date: "2026-08-01", ticket_no: 2 }), "2026-08-19")).toBe(
      "8/1 #2",
    );
  });
});

describe("todayInTaipei", () => {
  it("**用台北時區切日**：UTC 深夜已是台北隔天", () => {
    // 2026-08-18 17:00Z = 台北 2026-08-19 01:00
    expect(todayInTaipei(new Date("2026-08-18T17:00:00Z"))).toBe("2026-08-19");
  });

  it("台北凌晨與上午屬同一日", () => {
    expect(todayInTaipei(new Date("2026-08-18T16:30:00Z"))).toBe(
      todayInTaipei(new Date("2026-08-19T00:30:00Z")),
    );
  });
});
