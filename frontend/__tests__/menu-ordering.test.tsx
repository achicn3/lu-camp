// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MenuOrderingSection } from "@/features/menu/MenuOrderingSection";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("商品與分類排序各自儲存到原有 API", async () => {
  const writes: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (request.method === "PATCH") {
      writes.push({ url: request.url, body: await request.json() });
      return new Response(JSON.stringify({ id: 7, sort_order: 2 }), { headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify([{ id: 3, name: "咖啡", sort_order: 0 }]), { headers: { "Content-Type": "application/json" } });
  }));
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={query}><MenuOrderingSection items={[{ id: 7, name: "拿鐵", sort_order: 0 }]} onChanged={vi.fn()} /></QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(screen.getByText("菜單排序"));
  const category = await screen.findByLabelText("咖啡 分類排序");
  await user.clear(category); await user.type(category, "2");
  await user.click(screen.getByRole("button", { name: "儲存咖啡分類排序" }));
  const item = screen.getByLabelText("拿鐵 商品排序");
  await user.clear(item); await user.type(item, "8");
  await user.click(screen.getByRole("button", { name: "儲存拿鐵商品排序" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0].url).toMatch(/menu-categories\/3$/); expect(writes[0].body).toEqual({ sort_order: 2 });
  expect(writes[1].url).toMatch(/menu-items\/7$/); expect(writes[1].body).toEqual({ sort_order: 8 });
});
