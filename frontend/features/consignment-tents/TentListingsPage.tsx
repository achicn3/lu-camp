"use client";
// 「官網寄售帳篷」（docs/97 §6）：在庫的寄售品＋官網資料。店員、店長都能編輯；存檔後立刻推上官網，
// 背景工作也每分鐘推一次（內容沒變不推）。最上方顯示官網同步狀態。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { TentEditDialog } from "@/features/consignment-tents/TentEditDialog";
import { missingInfo } from "@/features/consignment-tents/tentText";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Tent = components["schemas"]["TentListingRead"];
type Status = components["schemas"]["TentPublishStatusRead"];

export const TENTS_KEY = ["online-tents"] as const;
export const TENTS_STATUS_KEY = ["online-tents", "status"] as const;

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : `$${formatNtd(parsed)}`;
}

function fullName(tent: Tent): string {
  return [tent.brand_name, tent.name].filter(Boolean).join(" ");
}

function SyncStatus({
  status,
  onPublish,
  publishing,
}: {
  status: Status;
  onPublish: () => void;
  publishing: boolean;
}) {
  if (!status.configured) {
    return (
      <p className="tents-sync tents-sync-off">
        尚未設定線上點餐雲端，官網不會更新（請聯絡管理者）。
      </p>
    );
  }
  const button = (
    <button type="button" className="btn-ghost" disabled={publishing} onClick={onPublish}>
      {publishing ? "更新中…" : "立即更新官網"}
    </button>
  );
  if (status.last_error) {
    return (
      <div className="tents-sync tents-sync-error" role="alert">
        <span>官網更新失敗：{status.last_error}</span>
        {button}
      </div>
    );
  }
  if (status.pending) {
    return (
      <div className="tents-sync tents-sync-pending">
        <span>有變更還沒上官網，約 1 分鐘內會自動更新。</span>
        {button}
      </div>
    );
  }
  return (
    <p className="tents-sync tents-sync-ok">
      官網已同步・{status.pushed_at ? formatTaipeiDateTime(status.pushed_at) : "—"}
    </p>
  );
}

function StateBadge({ tent }: { tent: Tent }) {
  if (!tent.published) return <span className="tents-badge">未上架</span>;
  if (tent.reserved) return <span className="tents-badge tents-badge-hold">上架・保留中</span>;
  return <span className="tents-badge tents-badge-on">上架中</span>;
}

export function TentListingsPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Tent | null>(null);

  const tents = useQuery({
    queryKey: TENTS_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/online-order/tents");
      if (!data) throw new Error("讀取寄售帳篷失敗，請重新整理");
      return data;
    },
  });

  const status = useQuery({
    queryKey: TENTS_STATUS_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/online-order/tents/status");
      if (!data) throw new Error("讀取官網同步狀態失敗");
      return data;
    },
    refetchInterval: 30_000,
  });

  const publish = useMutation({
    mutationFn: async () => {
      const { data } = await api.POST("/api/v1/online-order/tents/publish");
      if (!data) throw new Error("更新官網失敗");
      return data;
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: TENTS_STATUS_KEY }),
  });

  const rows = tents.data ?? [];
  return (
    <section className="tents-page">
      <div className="tents-head">
        <div>
          <h1 className="page-title">官網寄售帳篷</h1>
          <p className="hint">
            勾「上架官網」的寄售品會出現在官網寄售帳篷頁；售價、品牌、名稱照 POS 商品資料。
            賣掉後官網會移到「最近售出」並拿掉照片，30 天後消失。
          </p>
        </div>
      </div>

      {status.data && (
        <SyncStatus
          status={status.data}
          onPublish={() => publish.mutate()}
          publishing={publish.isPending}
        />
      )}

      {tents.isPending ? (
        <p className="hint">讀取中…</p>
      ) : tents.isError ? (
        <p role="alert" className="form-error">
          {tents.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">目前沒有在庫的寄售品。寄售入庫後會出現在這裡。</p>
      ) : (
        <div className="tents-table-wrap">
          <table className="data-table tents-table">
            <thead>
              <tr>
                <th>編號</th>
                <th>商品</th>
                <th>售價</th>
                <th>官網</th>
                <th>還缺</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((tent) => {
                const gaps = missingInfo(tent);
                return (
                  <tr key={tent.item_id}>
                    <td className="tents-code">{tent.item_code}</td>
                    <td>{fullName(tent)}</td>
                    <td className="money">{money(tent.listed_price)}</td>
                    <td>
                      <StateBadge tent={tent} />
                    </td>
                    <td className={gaps.length > 0 ? "tents-gaps" : "tents-gaps-none"}>
                      {gaps.length > 0 ? gaps.join("、") : "—"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-secondary"
                        aria-label={`編輯 ${tent.item_code}`}
                        onClick={() => setEditing(tent)}
                      >
                        編輯
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && (
        <TentEditDialog
          tent={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: TENTS_KEY });
            publish.mutate();
          }}
          onPhotosChanged={() => void queryClient.invalidateQueries({ queryKey: TENTS_KEY })}
        />
      )}
    </section>
  );
}
