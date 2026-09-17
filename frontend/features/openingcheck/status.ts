"use client";
// 開店前檢查的狀態來源（**頁面與導覽列共用**）。
//
// 分兩處各自判斷會出現「頁面說沒做完、選單說做完了」：後端的 `completed` 不含裝置
// （裝置狀態只有前端問得到），若導覽紅點只看它，標籤機離線時紅點會消失。
import { useQuery } from "@tanstack/react-query";

import { type AgentDevice, fetchDeviceStatus } from "@/lib/agent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

// 型別一律取自 OpenAPI 生成物（CLAUDE.md §3）：手寫一份等於把合約複製兩份，
// 日後後端改欄位，手寫的那份不會紅。
export type OpeningCheckToday = components["schemas"]["OpeningCheckTodayRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function deviceKey(device: AgentDevice): string {
  return `device:${device.kind}:${device.id}`;
}

/** 這台裝置今天是否「不能算通過」：離線、探測失敗，或跑在測試模式（沒接真機）。 */
export function devicePasses(device: AgentDevice): boolean {
  return device.online && device.driver !== "fake";
}

export function useOpeningCheckStatus(enabled: boolean) {
  const today = useQuery({
    queryKey: ["opening-check", "today"],
    enabled,
    queryFn: async (): Promise<OpeningCheckToday> => {
      // 讀取失敗一律丟例外：把「查不到」說成「沒有待處理」會讓紅點消失、當天不再提醒，
      // 店員以為都做完了（與發票待處理徽章同一條教訓）。
      const { data, error } = await api.GET("/api/v1/opening-check/today");
      if (!data) throw new Error(extractDetail(error) ?? "讀取開店前檢查失敗");
      return data;
    },
  });

  // 代理連不到（null）或回空清單都視為「問不到」：空清單會被誤讀成「沒有任何裝置」，
  // 畫面就會顯示全綠，等於謊報。
  const devices = useQuery({
    queryKey: ["opening-check", "devices"],
    enabled,
    queryFn: fetchDeviceStatus,
    refetchInterval: 30_000,
  });

  const deviceList = devices.data ?? null;
  // 「問不到」＝連不到代理，或代理回空清單（空清單會被誤讀成「沒有任何裝置」）。
  const devicesUnknown =
    devices.isFetched && (deviceList === null || deviceList.length === 0);
  const skipped = new Set(today.data?.skipped_keys ?? []);
  const failingDevices = (deviceList ?? []).filter(
    (device) => !devicePasses(device) && !skipped.has(deviceKey(device)),
  );

  return {
    today,
    devices,
    deviceList,
    devicesUnknown,
    skipped,
    // 頁面用：只有「三件事都明確知道」才敢說全綠——狀態讀到了、後端說完成、裝置查得到且都過。
    allClear:
      today.isSuccess && today.data.completed && !devicesUnknown && failingDevices.length === 0,
    // 紅點與自動導向用：只在**明確知道有東西沒做**時才提醒。
    //
    // 「問不到代理」不算沒做完：手機、平板上根本沒有代理（代理只跑在收銀電腦上），
    // 把它當成未完成的話，那些裝置會天天亮紅點、天天被導走，而且畫面上一列裝置都沒有
    // ——連略過的按鈕都按不到，永遠解不掉。天天亮的提醒等於沒有提醒。
    incomplete:
      today.isSuccess && (!today.data.completed || failingDevices.length > 0),
  };
}
