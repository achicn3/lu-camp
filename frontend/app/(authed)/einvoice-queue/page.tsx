"use client";
// /einvoice-queue 發票佇列（MANAGER）：待送出／失敗的開立、作廢、折讓一覽與人工處置。
//
// 為什麼需要這一頁：作廢（F0501）與折讓（G0401）是排進佇列、由背景送出的，開立（F0401）
// 則在結帳當下同步送。先前這三種訊息只要沒送成功，就**再也沒有任何畫面看得到**——
// 帳上作廢而平台上發票仍有效，沒有人會知道。這一頁是唯一的補救入口。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";

type QueueItem = components["schemas"]["EInvoiceQueueItemRead"];
type UploadStatus = QueueItem["status"];

const ACTION_LABELS: Record<QueueItem["action"], string> = {
  ISSUE: "開立",
  VOID: "作廢",
  ALLOWANCE: "折讓",
};

const STATUS_LABELS: Record<UploadStatus, string> = {
  PENDING: "待送出",
  UPLOADED: "已送出",
  FAILED: "平台退回",
  CANCELLED: "已中止",
};

/** 篩選頁籤：預設看「平台退回」——那是真的需要人處理的。 */
const FILTERS: { key: UploadStatus | "ALL"; label: string }[] = [
  { key: "FAILED", label: "平台退回" },
  { key: "PENDING", label: "待送出" },
  { key: "ALL", label: "全部" },
];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export default function EInvoiceQueuePage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<UploadStatus | "ALL">("FAILED");
  const [note, setNote] = useState<string | null>(null);

  const queue = useQuery({
    queryKey: ["einvoice-queue", filter],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/einvoice/queue", {
        params: { query: { ...(filter === "ALL" ? {} : { status: filter }), limit: 100 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取發票待處理清單失敗");
      return data;
    },
    refetchInterval: 30_000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["einvoice-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["einvoice-queue-badge"] });
  };

  const sendNow = useMutation({
    mutationFn: async (item: QueueItem) => {
      const { data, error } = await api.POST("/api/v1/einvoice/queue/{queue_id}/send", {
        params: { path: { queue_id: item.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "送出失敗");
      return data;
    },
    onSuccess: (data) => {
      setNote(
        data.status === "UPLOADED"
          ? `#${data.id} 已送交平台。`
          : `#${data.id} 平台未接受：${data.last_error ?? "未知原因"}（可重試）。`,
      );
      invalidate();
    },
    onError: (err: Error) => setNote(err.message),
  });

  const retry = useMutation({
    mutationFn: async (item: QueueItem) => {
      const { data, error } = await api.POST("/api/v1/einvoice/queue/{queue_id}/retry", {
        params: { path: { queue_id: item.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "重試失敗");
      return data;
    },
    onSuccess: (data) => {
      setNote(`#${data.id} 已排回待送出，接著按「立即送出」。`);
      invalidate();
    },
    onError: (err: Error) => setNote(err.message),
  });

  // 開立失敗的補救：POS 結帳頁對店員說過「可稍後補開」，在此兌現。
  // 後端 issue 是冪等且**對帳先行**的——送出去但回應斷掉的那種，再按一次會先向平台
  // 求證、認回原發票，不會重複開立。所以店員不需要自己判斷「到底開出去沒」。
  const reissue = useMutation({
    mutationFn: async (item: QueueItem) => {
      if (item.sale_id == null) throw new Error("這筆沒有對應的交易，無法補開");
      const { data, error } = await api.POST("/api/v1/einvoice/sales/{sale_id}/issue", {
        params: { path: { sale_id: item.sale_id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "開立失敗");
      return data;
    },
    onSuccess: (invoice) => {
      setNote(`發票已開立：${invoice.invoice_no ?? "（已取號）"}。`);
      invalidate();
    },
    onError: (err: Error) => setNote(err.message),
  });

  const busy = sendNow.isPending || retry.isPending || reissue.isPending;
  const items = queue.data?.items ?? [];

  // 開立（F0401）送出去就是**真的開一張發票給國稅局**，而待送出清單裡可能積著大量
  // 舊交易的開立列。手一滑就替一年前的交易補開發票——實測踩過。作廢與折讓沒有這個
  // 問題（它們只是把「已經作廢」的事實補送出去），故只對開立要求二次確認。
  const confirmIssue = (item: QueueItem): boolean =>
    window.confirm(
      `這會替交易 #${item.sale_id ?? "?"}（${formatTaipeiDateTime(item.created_at)} 建立）` +
        "真的開立一張電子發票給國稅局。\n\n舊交易通常不該再補開，確定要送出嗎？",
    );

  return (
    <main className="page">
      <h1>發票待處理</h1>
      <p className="muted">
        作廢與折讓由系統背景自動送交平台；這裡看得到還沒送成功的，以及平台退回、需要人處理的。
        <strong>在送出成功之前，平台上那張發票仍然有效。</strong>
      </p>

      <div className="row" role="group" aria-label="狀態篩選">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={filter === f.key ? "btn" : "btn-ghost"}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {note !== null && (
        <p role="status" className="form-note">
          {note}
        </p>
      )}
      {queue.isError && (
        <p role="alert" className="form-error">
          {(queue.error as Error).message}
        </p>
      )}

      <p className="muted">
        {queue.isLoading ? "讀取中…" : `共 ${queue.data?.total ?? 0} 筆`}
        {(queue.data?.total ?? 0) > items.length ? "（僅顯示最新 100 筆）" : ""}
      </p>

      <table className="table">
        <thead>
          <tr>
            <th>項目</th>
            <th>發票號碼</th>
            <th>交易</th>
            <th>狀態</th>
            <th>嘗試</th>
            <th>建立時間</th>
            <th>平台回覆</th>
            <th>處理</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{ACTION_LABELS[item.action]}</td>
              <td>{item.invoice_no ?? "—"}</td>
              <td>{item.sale_id != null ? `#${item.sale_id}` : "—"}</td>
              <td>{STATUS_LABELS[item.status]}</td>
              <td>{item.attempts}</td>
              <td>{formatTaipeiDateTime(item.created_at)}</td>
              <td className="wrap">{item.last_error ?? "—"}</td>
              <td>
                {item.status === "PENDING" && (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={busy}
                    aria-label={`立即送出第 ${item.id} 筆`}
                    onClick={() => {
                      if (item.action === "ISSUE" && !confirmIssue(item)) return;
                      setNote(null);
                      sendNow.mutate(item);
                    }}
                  >
                    立即送出
                  </button>
                )}
                {item.status === "FAILED" && (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={busy}
                    aria-label={`重試第 ${item.id} 筆`}
                    onClick={() => {
                      setNote(null);
                      retry.mutate(item);
                    }}
                  >
                    重試
                  </button>
                )}
                {item.action === "ISSUE" && item.status !== "UPLOADED" && item.sale_id != null && (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={busy}
                    aria-label={`重新開立銷售 ${item.sale_id} 的發票`}
                    onClick={() => {
                      if (!confirmIssue(item)) return;
                      setNote(null);
                      reissue.mutate(item);
                    }}
                  >
                    重新開立
                  </button>
                )}
              </td>
            </tr>
          ))}
          {!queue.isLoading && items.length === 0 && (
            <tr>
              <td colSpan={8} className="muted">
                沒有符合的項目。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </main>
  );
}
