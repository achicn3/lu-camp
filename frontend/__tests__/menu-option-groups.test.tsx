// @vitest-environment jsdom
// 餐飲選項群組管理（docs/44 §3.2；O2）：群組／選項的建立、修改、停售、每日限量、封存，
// 以及品項掛選項群組與介紹。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MenuItemEditDialog } from "@/features/menu/MenuItemEditDialog";
import { OptionGroupsSection, parseOptionLines } from "@/features/menu/OptionGroupsSection";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: unknown };

function stubFetch(route: (url: string, method: string) => Response | null): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const text =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      calls.push({ url, method, body: text ? (JSON.parse(text) as unknown) : null });
      const resp = route(url, method);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
  return calls;
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const option = (id: number, group: number, name: string, delta: string, extra = {}) => ({
  id,
  group_id: group,
  name,
  price_delta: delta,
  unit_cost: null,
  is_available: true,
  sort_order: id,
  daily_limited: false,
  remaining: null,
  ...extra,
});

const GROUPS = [
  {
    id: 1,
    name: "溫度",
    min_select: 1,
    max_select: 1,
    sort_order: 0,
    options: [option(11, 1, "熱", "0"), option(12, 1, "冰", "0")],
  },
  {
    id: 2,
    name: "加購",
    min_select: 0,
    max_select: 2,
    sort_order: 1,
    options: [option(21, 2, "燕麥奶", "20", { unit_cost: "8" })],
  },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("parseOptionLines", () => {
  it("一行一個選項；結尾的 +N／-N 是加價，沒寫就是 0；空行略過", () => {
    expect(parseOptionLines("熱\n冰\n\n燕麥奶 +20\n少冰 -5\n2號豆")).toEqual([
      { name: "熱", price_delta: "0" },
      { name: "冰", price_delta: "0" },
      { name: "燕麥奶", price_delta: "20" },
      { name: "少冰", price_delta: "-5" },
      { name: "2號豆", price_delta: "0" },
    ]);
  });
});

describe("選項群組管理", () => {
  it("列出群組與選項（加價、成本）", async () => {
    stubFetch((url) => (url.includes("/menu-option-groups") ? json(GROUPS) : null));
    wrap(<OptionGroupsSection />);
    const group = await screen.findByRole("region", { name: "溫度" });
    expect(within(group).getByText("必選 1 項")).toBeTruthy();
    const extra = screen.getByRole("region", { name: "加購" });
    expect(within(extra).getByText("可不選，最多 2 項")).toBeTruthy();
    expect(within(extra).getByLabelText("燕麥奶 加價")).toHaveProperty("value", "20");
    expect(within(extra).getByLabelText("燕麥奶 成本")).toHaveProperty("value", "8");
  });

  it("新增群組：名稱、至少／最多、選項一行一個", async () => {
    const calls = stubFetch((url, method) => {
      if (url.endsWith("/menu-option-groups") && method === "POST")
        return json({ ...GROUPS[0], id: 9 }, 201);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const user = userEvent.setup();
    wrap(<OptionGroupsSection />);
    await screen.findByRole("region", { name: "溫度" });
    const form = screen.getByRole("form", { name: "新增選項群組" });
    await user.type(within(form).getByLabelText("群組名稱"), "甜度");
    await user.clear(within(form).getByLabelText("至少選"));
    await user.type(within(form).getByLabelText("至少選"), "1");
    await user.clear(within(form).getByLabelText("最多選"));
    await user.type(within(form).getByLabelText("最多選"), "1");
    await user.type(within(form).getByLabelText("選項（一行一個）"), "正常{enter}半糖{enter}加蜂蜜 +10");
    await user.click(within(form).getByRole("button", { name: "新增群組" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        name: "甜度",
        min_select: 1,
        max_select: 1,
        sort_order: 0,
        options: [
          { name: "正常", price_delta: "0" },
          { name: "半糖", price_delta: "0" },
          { name: "加蜂蜜", price_delta: "10" },
        ],
      }),
    );
  });

  it("至少選大於最多選：擋下不送", async () => {
    const calls = stubFetch((url) => (url.includes("/menu-option-groups") ? json(GROUPS) : null));
    const user = userEvent.setup();
    wrap(<OptionGroupsSection />);
    await screen.findByRole("region", { name: "溫度" });
    const form = screen.getByRole("form", { name: "新增選項群組" });
    await user.type(within(form).getByLabelText("群組名稱"), "配料");
    await user.clear(within(form).getByLabelText("至少選"));
    await user.type(within(form).getByLabelText("至少選"), "3");
    await user.clear(within(form).getByLabelText("最多選"));
    await user.type(within(form).getByLabelText("最多選"), "2");
    await user.click(within(form).getByRole("button", { name: "新增群組" }));
    expect(await within(form).findByRole("alert")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("選項：改加價／成本、停售、每日限量、新增選項", async () => {
    const calls = stubFetch((url, method) => {
      if (url.includes("/menu-options/21") && method === "PATCH")
        return json(option(21, 2, "燕麥奶", "25"));
      if (url.includes("/menu-option-groups/2/options") && method === "POST")
        return json(option(22, 2, "濃縮", "30"), 201);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const user = userEvent.setup();
    wrap(<OptionGroupsSection />);
    const extra = await screen.findByRole("region", { name: "加購" });
    const delta = within(extra).getByLabelText("燕麥奶 加價");
    await user.clear(delta);
    await user.type(delta, "25");
    const cost = within(extra).getByLabelText("燕麥奶 成本");
    await user.clear(cost);
    await user.click(within(extra).getByRole("button", { name: "燕麥奶 儲存" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        price_delta: "25",
        unit_cost: null,
      }),
    );
    await user.click(within(extra).getByRole("checkbox", { name: "燕麥奶 可售" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({
        is_available: false,
      }),
    );
    await user.click(within(extra).getByRole("checkbox", { name: "燕麥奶 每日限量" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({
        daily_limited: true,
      }),
    );
    await user.type(within(extra).getByLabelText("加購 新選項"), "濃縮 +30");
    await user.click(within(extra).getByRole("button", { name: "加購 新增選項" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        name: "濃縮",
        price_delta: "30",
      }),
    );
  });

  it("改群組的至少／最多選", async () => {
    const calls = stubFetch((url, method) => {
      if (url.includes("/menu-option-groups/2") && method === "PATCH") return json(GROUPS[1]);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const user = userEvent.setup();
    wrap(<OptionGroupsSection />);
    const extra = await screen.findByRole("region", { name: "加購" });
    await user.click(within(extra).getByRole("button", { name: "改規則" }));
    const max = within(extra).getByLabelText("加購 最多選");
    await user.clear(max);
    await user.type(max, "3");
    await user.click(within(extra).getByRole("button", { name: "加購 儲存規則" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        name: "加購",
        min_select: 0,
        max_select: 3,
      }),
    );
  });

  it("封存選項與群組要先確認", async () => {
    const calls = stubFetch((url, method) => {
      if (method === "DELETE") return json(url.includes("menu-options") ? GROUPS[1].options[0] : GROUPS[1]);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const user = userEvent.setup();
    wrap(<OptionGroupsSection />);
    const extra = await screen.findByRole("region", { name: "加購" });
    await user.click(within(extra).getByRole("button", { name: "燕麥奶 移除" }));
    await user.click(await screen.findByRole("button", { name: "移除" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url.includes("/menu-options/21"))).toBe(
        true,
      ),
    );
    await user.click(within(extra).getByRole("button", { name: "移除群組" }));
    await user.click(await screen.findByRole("button", { name: "移除" }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "DELETE" && c.url.endsWith("/menu-option-groups/2")),
      ).toBe(true),
    );
  });
});

describe("品項編輯（品名、分類、售價、成本、介紹、選項；店主 2026-10-09）", () => {
  const ITEM = {
    id: 5,
    store_id: 1,
    name: "拿鐵",
    unit_price: "150",
    unit_cost: null,
    category: "咖啡",
    category_id: 1,
    description: null,
    photo_sha256: null,
    is_available: true,
    sort_order: 0,
    daily_limited: false,
    remaining: null,
    stock_set_today: false,
    option_groups: [GROUPS[1]],
  };

  it("勾選要掛的群組（依勾選順序）、填介紹 → 存檔", async () => {
    const calls = stubFetch((url, method) => {
      if (url.includes("/menu-items/5/option-groups") && method === "PUT") return json(ITEM);
      if (url.includes("/menu-items/5") && method === "PATCH") return json(ITEM);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<MenuItemEditDialog item={ITEM} onDone={onDone} onCancel={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: /拿鐵/ });
    const extra = await within(dialog).findByRole("checkbox", { name: /加購/ });
    expect((extra as HTMLInputElement).checked).toBe(true);
    await user.click(within(dialog).getByRole("checkbox", { name: /溫度/ }));
    await user.type(within(dialog).getByLabelText("介紹"), "濃縮加鮮奶");
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ group_ids: [2, 1] });
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ description: "濃縮加鮮奶" });
  });

  it("沒改介紹就不送 PATCH；清空介紹送 null", async () => {
    const calls = stubFetch((url, method) => {
      if (method === "PUT") return json(ITEM);
      if (method === "PATCH") return json(ITEM);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(
      <MenuItemEditDialog
        item={{ ...ITEM, description: "舊介紹" }}
        onDone={onDone}
        onCancel={() => {}}
      />,
    );
    const dialog = await screen.findByRole("dialog", { name: /拿鐵/ });
    await within(dialog).findByRole("checkbox", { name: /溫度/ });
    await user.clear(within(dialog).getByLabelText("介紹"));
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ description: null });
  });

  it("帶出現有的品名、分類、售價、成本；改了的一次送出", async () => {
    const calls = stubFetch((url, method) => {
      if (method === "PATCH") return json(ITEM);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<MenuItemEditDialog item={{ ...ITEM, unit_cost: "40" }} onDone={onDone} onCancel={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: "編輯 拿鐵" });
    await within(dialog).findByRole("checkbox", { name: /溫度/ });
    expect((within(dialog).getByLabelText("品名") as HTMLInputElement).value).toBe("拿鐵");
    expect((within(dialog).getByLabelText("分類") as HTMLInputElement).value).toBe("咖啡");
    expect((within(dialog).getByLabelText("售價") as HTMLInputElement).value).toBe("150");
    expect((within(dialog).getByLabelText("成本") as HTMLInputElement).value).toBe("40");
    await user.clear(within(dialog).getByLabelText("品名"));
    await user.type(within(dialog).getByLabelText("品名"), "燕麥拿鐵");
    await user.clear(within(dialog).getByLabelText("分類"));
    await user.type(within(dialog).getByLabelText("分類"), "特調");
    await user.clear(within(dialog).getByLabelText("售價"));
    await user.type(within(dialog).getByLabelText("售價"), "170");
    await user.clear(within(dialog).getByLabelText("成本"));
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const patches = calls.filter((c) => c.method === "PATCH");
    expect(patches).toHaveLength(1);
    expect(patches[0]?.body).toEqual({
      name: "燕麥拿鐵",
      category: "特調",
      unit_price: "170",
      unit_cost: null,
    });
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("清空分類送 null；什麼都沒改就不送", async () => {
    const calls = stubFetch((url, method) => {
      if (method === "PATCH") return json(ITEM);
      if (url.includes("/menu-option-groups")) return json(GROUPS);
      return null;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<MenuItemEditDialog item={ITEM} onDone={onDone} onCancel={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: "編輯 拿鐵" });
    await within(dialog).findByRole("checkbox", { name: /溫度/ });
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    await user.clear(within(dialog).getByLabelText("分類"));
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(2));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ category: null });
  });

  it("品名空白、售價不是正整數、成本負數：擋下不送", async () => {
    const calls = stubFetch((url) =>
      url.includes("/menu-option-groups") ? json(GROUPS) : null,
    );
    const user = userEvent.setup();
    wrap(<MenuItemEditDialog item={ITEM} onDone={() => {}} onCancel={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: "編輯 拿鐵" });
    await within(dialog).findByRole("checkbox", { name: /溫度/ });
    const save = within(dialog).getByRole("button", { name: "儲存" });

    await user.clear(within(dialog).getByLabelText("品名"));
    await user.click(save);
    expect((await within(dialog).findByRole("alert")).textContent).toBe("請輸入品名");
    await user.type(within(dialog).getByLabelText("品名"), "拿鐵");
    expect(within(dialog).queryByRole("alert")).toBeNull(); // 改了欄位就收掉舊錯誤

    await user.clear(within(dialog).getByLabelText("售價"));
    await user.type(within(dialog).getByLabelText("售價"), "0");
    await user.click(save);
    expect(within(dialog).getByRole("alert").textContent).toBe("售價須為正整數元");
    await user.clear(within(dialog).getByLabelText("售價"));
    await user.type(within(dialog).getByLabelText("售價"), "150");

    await user.type(within(dialog).getByLabelText("成本"), "-5");
    await user.click(save);
    expect(within(dialog).getByRole("alert").textContent).toBe("成本須為 0 以上的整數元");
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });
});
