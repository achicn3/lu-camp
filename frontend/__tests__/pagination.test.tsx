// @vitest-environment jsdom
// 共用分頁：有總筆數就照它算頁數。沒有總筆數時只能用「這頁剛好滿＝可能還有下一頁」猜，
// 資料剛好是整頁倍數時會多出一個點得下去的空白頁——全站清單頁都踩過這個。
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Pagination } from "@/features/common/Pagination";

afterEach(cleanup);

describe("Pagination", () => {
  it("有總筆數時顯示第 X / Y 頁，剛好整頁也不會多出一頁", async () => {
    const onPage = vi.fn();
    render(<Pagination page={0} count={50} pageSize={50} total={50} onPage={onPage} />);
    // 剛好 50 筆＝只有一頁：只有一頁時整個控制不顯示。
    expect(screen.queryByRole("button", { name: /下一頁/ })).toBeNull();
  });

  it("沒有總筆數時沿用舊推測（滿頁即可能有下一頁）", () => {
    render(<Pagination page={0} count={50} pageSize={50} onPage={vi.fn()} />);
    expect(screen.getByRole("button", { name: /下一頁/ })).toBeDefined();
    expect(screen.getByText("第 1 頁")).toBeDefined();
  });

  it("最後一頁的下一頁按不下去，上一頁可以回去", async () => {
    const onPage = vi.fn();
    render(<Pagination page={1} count={50} pageSize={50} total={100} onPage={onPage} />);
    expect(screen.getByText("第 2 / 2 頁・共 100 筆")).toBeDefined();
    expect(screen.getByRole("button", { name: /下一頁/ }).hasAttribute("disabled")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /上一頁/ }));
    expect(onPage).toHaveBeenCalledWith(0);
  });

  it("單位可換（人／張／家…），讓每一頁講自己的話", () => {
    render(<Pagination page={0} count={20} pageSize={20} total={41} unit="人" onPage={vi.fn()} />);
    expect(screen.getByText("第 1 / 3 頁・共 41 人")).toBeDefined();
  });
});
