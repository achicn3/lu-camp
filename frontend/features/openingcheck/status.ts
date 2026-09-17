"use client";
// 開店前檢查的狀態來源（**頁面與導覽列共用**）。
//
// 分兩處各自判斷會出現「頁面說沒做完、選單說做完了」：後端的 `completed` 不含裝置
// （裝置狀態只有前端問得到），若導覽紅點只看它，標籤機離線時紅點會消失。
import { useQuery } from "@tanstack/react-query";

import { type AgentDevice, fetchDeviceStatus } from "@/lib/agent";
import { api } from "@/lib/api";

export type OpeningCheckToday = {
  business_date: string;
  cash_session_state: "OPEN_TODAY" | "STALE" | "NONE";
  cash_session_open: boolean;
  items: { id: number; label: string; href: string | null; done: boolean }[];
  skipped_keys: string[];
  completed: boolean;
};

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
      return data as OpeningCheckToday;
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
  const devicesUnknown =
    devices.isFetched && (deviceList === null || deviceList.length === 0);
  const skipped = new Set(today.data?.skipped_keys ?? []);
  const devicesOk = (deviceList ?? []).every(
    (device) => devicePasses(device) || skipped.has(deviceKey(device)),
  );

  return {
    today,
    devices,
    deviceList,
    devicesUnknown,
    skipped,
    // 只有「三件事都明確知道」才算完成：後端狀態讀到了、後端說完成、裝置查得到且都過。
    // 任何一項不確定都不算——寧可多提醒一次，也不要謊報綠燈。
    allClear: today.isSuccess && today.data.completed && !devicesUnknown && devicesOk,
    // 明確知道「還沒做完」才顯示紅點；讀不到狀態時不顯示（那是錯誤，另外報）。
    incomplete: today.isSuccess && !(today.data.completed && !devicesUnknown && devicesOk),
  };
}
