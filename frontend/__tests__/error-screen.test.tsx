// @vitest-environment jsdom
// 錯誤畫面（2026-10-26 LINE Pay 結帳後整頁變成 Next 預設的黑底「Application error」，
// 看不到原因）：要把實際的錯誤內容顯示出來讓店員拍照，並給重新載入／回 POS 的出口。
// 本專案不使用 jest-dom matchers，沿用 vanilla 斷言（toBeTruthy / textContent）。
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import AuthedError from "@/app/(authed)/error";
import GlobalError from "@/app/global-error";
import { ErrorScreen, describeError } from "@/features/common/ErrorScreen";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function sampleError(): Error & { digest?: string } {
  const error = new TypeError("Cannot read properties of undefined (reading 'carrier_id')");
  error.stack = [
    "TypeError: Cannot read properties of undefined (reading 'carrier_id')",
    ...Array.from({ length: 20 }, (_, i) => `    at frame${i} (chunk.js:1:${i})`),
  ].join("\n");
  return Object.assign(error, { digest: "1234567890" });
}

describe("describeError", () => {
  it("取出名稱、訊息、digest，堆疊只留前幾行", () => {
    const info = describeError(sampleError());
    expect(info.name).toBe("TypeError");
    expect(info.message).toBe("Cannot read properties of undefined (reading 'carrier_id')");
    expect(info.digest).toBe("1234567890");
    expect(info.stackLines.length).toBe(8);
    expect(info.stackLines[0]).toBe("at frame0 (chunk.js:1:0)");
  });

  it("Safari 格式的堆疊（函式@網址）也取得到", () => {
    const error = new TypeError("undefined is not an object");
    error.stack = "render@http://192.168.20.181:3000/_next/static/chunks/a.js:1:200\nx@b.js:2:3";
    const info = describeError(error);
    expect(info.stackLines).toEqual([
      "render@http://192.168.20.181:3000/_next/static/chunks/a.js:1:200",
      "x@b.js:2:3",
    ]);
  });

  it("沒有訊息、沒有堆疊也不會再壞一次", () => {
    const error = new Error("");
    error.stack = undefined;
    const info = describeError(error);
    expect(info.message).toBe("（沒有錯誤訊息）");
    expect(info.stackLines).toEqual([]);
    expect(info.digest).toBeNull();
  });
});

describe("ErrorScreen", () => {
  it("顯示實際錯誤內容與拍照提醒", () => {
    render(<ErrorScreen error={sampleError()} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("TypeError");
    expect(text).toContain("reading 'carrier_id'");
    expect(text).toContain("1234567890");
    expect(text).toContain("at frame7");
    expect(text).not.toContain("at frame8");
    expect(text).toContain("拍照");
  });

  it("按「重新載入」整頁重新載入（不用 unstable_retry：快取的壞資料會讓它原地再壞）", async () => {
    const onReload = vi.fn();
    render(<ErrorScreen error={sampleError()} onReload={onReload} />);
    await userEvent.click(screen.getByRole("button", { name: "重新載入" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("「回到 POS 結帳」是整頁導向 /pos", () => {
    render(<ErrorScreen error={sampleError()} />);
    const link = screen.getByRole("link", { name: "回到 POS 結帳" });
    expect(link.getAttribute("href")).toBe("/pos");
  });
});

describe("app/(authed)/error.tsx", () => {
  it("顯示錯誤頁，並把錯誤印到主控台", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const error = sampleError();
    render(<AuthedError error={error} />);
    expect(consoleError).toHaveBeenCalledWith(error);
    expect(screen.getByRole("button", { name: "重新載入" })).toBeTruthy();
  });
});

describe("app/global-error.tsx", () => {
  it("自帶 html/body，並顯示錯誤內容", () => {
    const html = renderToStaticMarkup(
      <GlobalError error={sampleError()} />,
    );
    expect(html.startsWith("<html")).toBe(true);
    expect(html).toContain("reading &#x27;carrier_id&#x27;");
  });
});
