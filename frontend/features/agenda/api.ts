"use client";
// 行事曆的資料讀寫（docs/99）。所有寫入成功後一律讓 ["agenda"] 底下的查詢重抓：
// 月曆、清單、今天、導覽列數字看的是同一份資料，各自更新會出現「這裡打勾了、那裡還沒」。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  AgendaEvent,
  AgendaItem,
  AgendaSeries,
  AgendaTask,
  AgendaToday,
  StaffMember,
} from "@/features/agenda/calendar";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

export const AGENDA_KEY = ["agenda"] as const;

type TaskStatus = components["schemas"]["AgendaTaskStatus"];

/** 後端擋下的原因照實給店員看。422 的欄位錯誤是陣列，取第一則並拿掉框架前綴。 */
export function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg.replace(/^Value error, /, "");
    }
  }
  return fallback;
}

export function useAgendaItems(start: string, end: string) {
  return useQuery({
    queryKey: [...AGENDA_KEY, "items", start, end],
    queryFn: async (): Promise<AgendaItem[]> => {
      const { data, error } = await api.GET("/api/v1/agenda/items", {
        params: { query: { start, end } },
      });
      if (!data) throw new Error(errorMessage(error, "讀取行事曆失敗"));
      return data.items;
    },
  });
}

export function useAgendaToday(enabled = true) {
  return useQuery({
    queryKey: [...AGENDA_KEY, "today"],
    enabled,
    // 跨過午夜、別台裝置打勾後要跟上；一分鐘一次足夠，不必更頻繁。
    refetchInterval: 60_000,
    queryFn: async (): Promise<AgendaToday> => {
      // 讀取失敗要丟例外：把「查不到」說成「今天沒事」，導覽列數字會默默消失。
      const { data, error } = await api.GET("/api/v1/agenda/today");
      if (!data) throw new Error(errorMessage(error, "讀取今天的行事曆失敗"));
      return data;
    },
  });
}

export function useUndatedTasks() {
  return useQuery({
    queryKey: [...AGENDA_KEY, "undated"],
    queryFn: async (): Promise<AgendaTask[]> => {
      const { data, error } = await api.GET("/api/v1/agenda/tasks/undated");
      if (!data) throw new Error(errorMessage(error, "讀取沒有日期的待辦失敗"));
      return data;
    },
  });
}

export function useTaskSeries() {
  return useQuery({
    queryKey: [...AGENDA_KEY, "series"],
    queryFn: async (): Promise<AgendaSeries[]> => {
      const { data, error } = await api.GET("/api/v1/agenda/task-series");
      if (!data) throw new Error(errorMessage(error, "讀取重複的待辦失敗"));
      return data;
    },
  });
}

export function useAgendaEvent(eventId: number | null) {
  return useQuery({
    queryKey: [...AGENDA_KEY, "event", eventId],
    enabled: eventId !== null,
    queryFn: async (): Promise<AgendaEvent> => {
      const { data, error } = await api.GET("/api/v1/agenda/events/{event_id}", {
        params: { path: { event_id: eventId ?? 0 } },
      });
      if (!data) throw new Error(errorMessage(error, "讀取事件失敗"));
      return data;
    },
  });
}

export function useAgendaTask(taskId: number | null) {
  return useQuery({
    queryKey: [...AGENDA_KEY, "task", taskId],
    enabled: taskId !== null,
    queryFn: async (): Promise<AgendaTask> => {
      const { data, error } = await api.GET("/api/v1/agenda/tasks/{task_id}", {
        params: { path: { task_id: taskId ?? 0 } },
      });
      if (!data) throw new Error(errorMessage(error, "讀取待辦失敗"));
      return data;
    },
  });
}

export function useStaffMembers(includeInactive = false) {
  return useQuery({
    queryKey: ["staff-members", includeInactive],
    queryFn: async (): Promise<StaffMember[]> => {
      const { data, error } = await api.GET("/api/v1/staff-members", {
        params: { query: { include_inactive: includeInactive } },
      });
      if (!data) throw new Error(errorMessage(error, "讀取員工名單失敗"));
      return data;
    },
  });
}

/** 用編號改一筆待辦的狀態（「沒有日期」清單用；行事曆上的列用 useItemStatus）。 */
export function useTaskStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ taskId, status }: { taskId: number; status: TaskStatus }) => {
      const { data, error } = await api.POST("/api/v1/agenda/tasks/{task_id}/status", {
        params: { path: { task_id: taskId } },
        body: { status },
      });
      if (!data) throw new Error(errorMessage(error, "更新失敗"));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: AGENDA_KEY }),
  });
}

/** 行事曆上一列的打勾／略過：單次待辦與已建列的那一次走 task_id，還沒建列的走規則＋日期。 */
export function useItemStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ item, status }: { item: AgendaItem; status: TaskStatus }) => {
      if (item.task_id !== null && item.task_id !== undefined) {
        const { data, error } = await api.POST("/api/v1/agenda/tasks/{task_id}/status", {
          params: { path: { task_id: item.task_id } },
          body: { status },
        });
        if (!data) throw new Error(errorMessage(error, "更新失敗"));
        return data;
      }
      if (item.series_id === null || item.series_id === undefined || !item.occurrence_on) {
        throw new Error("這一列不是待辦");
      }
      const { data, error } = await api.POST(
        "/api/v1/agenda/task-series/{series_id}/occurrences/{occurrence_on}/status",
        {
          params: { path: { series_id: item.series_id, occurrence_on: item.occurrence_on } },
          body: { status },
        },
      );
      if (!data) throw new Error(errorMessage(error, "更新失敗"));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: AGENDA_KEY }),
  });
}

export function useSkipOverdue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (seriesId: number) => {
      const { data, error } = await api.POST(
        "/api/v1/agenda/task-series/{series_id}/skip-overdue",
        { params: { path: { series_id: seriesId } } },
      );
      if (!data) throw new Error(errorMessage(error, "略過失敗"));
      return data.skipped;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: AGENDA_KEY }),
  });
}

/** 刪除待辦、重複待辦或事件（後端都是封存）。 */
export function useAgendaDelete() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (
      target: { kind: "task"; id: number } | { kind: "series"; id: number } | { kind: "event"; id: number },
    ) => {
      const result =
        target.kind === "task"
          ? await api.DELETE("/api/v1/agenda/tasks/{task_id}", {
              params: { path: { task_id: target.id } },
            })
          : target.kind === "series"
            ? await api.DELETE("/api/v1/agenda/task-series/{series_id}", {
                params: { path: { series_id: target.id } },
              })
            : await api.DELETE("/api/v1/agenda/events/{event_id}", {
                params: { path: { event_id: target.id } },
              });
      if (!result.response.ok) throw new Error(errorMessage(result.error, "刪除失敗"));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: AGENDA_KEY }),
  });
}

/** 取消或恢復重複事件的某一次（例：這個週一照常營業）。 */
export function useEventOccurrence() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ eventId, on, cancelled }: { eventId: number; on: string; cancelled: boolean }) => {
      const { data, error } = await api.POST("/api/v1/agenda/events/{event_id}/occurrences", {
        params: { path: { event_id: eventId } },
        body: { on, cancelled },
      });
      if (!data) throw new Error(errorMessage(error, "更新失敗"));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: AGENDA_KEY }),
  });
}
