// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { OnlineHoursSection } from "@/features/menu/OnlineHoursSection";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("店長設定每週跨午夜時段，儲存後提醒發佈", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    if (request.method === "PATCH") {
      const body = await request.json(); bodies.push(body);
      return Response.json(body);
    }
    return Response.json({ online_order_hours: {enabled:false,days:Array(7).fill(null)} });
  }));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
    <OnlineHoursSection />
  </QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(screen.getByText("營業時間"));
  await user.click(await screen.findByLabelText("依營業時間接單"));
  await user.click(screen.getByLabelText("星期一 營業"));
  await user.clear(screen.getByLabelText("星期一 開始"));
  await user.type(screen.getByLabelText("星期一 開始"), "20:00");
  await user.clear(screen.getByLabelText("星期一 結束"));
  await user.type(screen.getByLabelText("星期一 結束"), "02:00");
  await user.click(screen.getByRole("button", {name:"儲存營業時間"}));
  expect(await screen.findByText(/已儲存，請按「發佈到線上點餐」/)).toBeTruthy();
  expect(bodies).toEqual([{online_order_hours:{enabled:true,days:[{opens:"20:00",closes:"02:00"},...Array(6).fill(null)]}}]);
});
