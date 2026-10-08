"use client";
// 菜單頁「線上點餐」區塊（docs/44 §3.5、§4.1；O3b）：把目前菜單發佈到客人掃碼的線上點餐，
// 列出各桌 QR 網址，可單桌重發（舊 QR 立刻失效）。MANAGER 專用（菜單頁本身已擋）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import { formatTaipeiDateTime } from "@/lib/datetime";

const STATUS_KEY = ["online-order", "status"] as const;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function OnlinePublishPanel() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rotating, setRotating] = useState<string | null>(null);

  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/online-order/status");
      if (!data) throw new Error(extractDetail(err) ?? "讀取線上點餐狀態失敗");
      return data;
    },
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: STATUS_KEY });

  const publish = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.POST("/api/v1/online-order/publish");
      if (!data) throw new Error(extractDetail(err) ?? "發佈失敗");
      return data;
    },
    onSuccess: (data) => {
      setError(null);
      const photos = data.photos_pushed > 0 ? `（新照片 ${data.photos_pushed} 張）` : "";
      setNotice(`已發佈 ${data.item_count} 道菜${photos}，客人重新整理就會看到。`);
      refresh();
    },
    onError: (err: Error) => {
      setNotice(null);
      setError(err.message);
    },
  });

  const rotate = useMutation({
    mutationFn: async (label: string) => {
      const { data, error: err } = await api.POST("/api/v1/online-order/tables/{label}/rotate", {
        params: { path: { label } },
      });
      if (!data) throw new Error(extractDetail(err) ?? "重發失敗");
      return data;
    },
    onSuccess: (data) => {
      setRotating(null);
      setError(null);
      setNotice(`${data.label} 的 QR 已重發，舊的 QR 已失效，請重印這一桌。`);
      refresh();
    },
    onError: (err: Error) => {
      setRotating(null);
      setError(err.message);
    },
  });

  const s = status.data;
  const configured = s?.configured === true;

  return (
    <section className="card online-publish" aria-labelledby="online-publish-title">
      <h2 id="online-publish-title">線上點餐</h2>
      {status.isError && (
        <p role="alert" className="form-error">
          {status.error.message}
        </p>
      )}
      {s !== undefined && !configured && (
        <p className="hint">尚未設定線上點餐的雲端網址與密鑰，請聯絡管理者。</p>
      )}
      {s !== undefined && configured && (
        <p className="hint">
          {s.last_published_at
            ? `上次發佈：${formatTaipeiDateTime(s.last_published_at)}`
            : "還沒發佈過。"}
          改了菜單、價格或照片後，按一次發佈客人才看得到。
        </p>
      )}
      <button
        type="button"
        className="btn-primary"
        disabled={!configured || publish.isPending}
        onClick={() => publish.mutate()}
      >
        {publish.isPending ? "發佈中…" : "發佈到線上點餐"}
      </button>
      {notice !== null && <p role="status">{notice}</p>}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {s !== undefined && s.tables.length > 0 && (
        <details className="online-publish-tables">
        <summary>客人掃碼的網址與重發 QR（{s.tables.length} 個）</summary>
        <div className="inv-table-wrap">
          <table className="inv-table">
            <thead>
              <tr>
                <th>桌號</th>
                <th>客人掃碼的網址</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {s.tables.map((t) => (
                <tr key={t.code}>
                  <td>{t.label}</td>
                  <td className="online-publish-url">{t.url}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`${t.label} 重發 QR`}
                      disabled={!configured || rotate.isPending}
                      onClick={() => setRotating(t.label)}
                    >
                      重發 QR
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </details>
      )}
      {rotating !== null && (
        <ConfirmDialog
          title={`重發 ${rotating} 的 QR`}
          confirmLabel="重發"
          danger
          busy={rotate.isPending}
          body={
            <p>
              重發後<strong>舊的 QR 立刻不能點餐</strong>，這一桌要重印新的 QR。通常只有懷疑 QR 被拍走、
              有人在店外亂下單時才需要重發。
            </p>
          }
          onConfirm={() => rotate.mutate(rotating)}
          onCancel={() => setRotating(null)}
        />
      )}
    </section>
  );
}
