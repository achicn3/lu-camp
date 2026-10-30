"use client";
// 「官網寄售帳篷」（docs/97 §6）：在庫的寄售品＋官網資料。店員、店長都能編輯；存檔後立刻推上官網，
// 背景工作也每分鐘推一次（內容沒變不推）。最上方顯示官網同步狀態。
// 預售（docs/102）：帳篷還在賣家家裡就先上官網，跟在庫的列在一起、標「預售」。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { TentEditDialog } from "@/features/consignment-tents/TentEditDialog";
import { TentPresaleDialog } from "@/features/consignment-tents/TentPresaleDialog";
import { missingInfo } from "@/features/consignment-tents/tentText";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Tent = components["schemas"]["TentListingRead"];
type Status = components["schemas"]["TentPublishStatusRead"];
type Presale = components["schemas"]["TentPresaleRead"];

export const TENTS_KEY = ["online-tents"] as const;
export const PRESALES_KEY = ["online-tents", "presales"] as const;
export const TENTS_STATUS_KEY = ["online-tents", "status"] as const;

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : `$${formatNtd(parsed)}`;
}

function fullName(tent: Pick<Tent, "brand_name" | "name">): string {
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

function StateBadge({ tent }: { tent: Pick<Tent, "published" | "reserved"> }) {
  if (!tent.published) return <span className="tents-badge">未上架</span>;
  if (tent.reserved) return <span className="tents-badge tents-badge-hold">上架・保留中</span>;
  return <span className="tents-badge tents-badge-on">上架中</span>;
}

export function TentListingsPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Tent | null>(null);
  // undefined＝沒開；null＝新增
  const [editingPresale, setEditingPresale] = useState<Presale | null | undefined>(undefined);
  const [cancelling, setCancelling] = useState<Presale | null>(null);

  const tents = useQuery({
    queryKey: TENTS_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/online-order/tents");
      if (!data) throw new Error("讀取寄售帳篷失敗，請重新整理");
      return data;
    },
  });

  const presales = useQuery({
    queryKey: PRESALES_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/online-order/tents/presales");
      if (!data) throw new Error("讀取預售失敗，請重新整理");
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

  const cancelPresale = useMutation({
    mutationFn: async (listingId: number) => {
      const { response } = await api.POST(
        "/api/v1/online-order/tents/presales/{listing_id}/cancel",
        { params: { path: { listing_id: listingId } } },
      );
      if (!response.ok) throw new Error("取消預售失敗，請重新整理後再試");
    },
    onSuccess: () => {
      setCancelling(null);
      void queryClient.invalidateQueries({ queryKey: PRESALES_KEY });
      publish.mutate();
    },
  });

  const rows = tents.data ?? [];
  const presaleRows = presales.data ?? [];
  return (
    <section className="tents-page">
      <div className="tents-head">
        <div>
          <h1 className="page-title">官網寄售帳篷</h1>
          <p className="hint">
            勾「上架官網」的寄售品會出現在官網寄售帳篷頁；售價、品牌、名稱照 POS 商品資料。
            照片、影片、顏色、露數都可以不填，不填也能上架。賣掉後官網會移到「最近售出」並拿掉照片與影片，30 天後消失。
          </p>
          <p className="hint">
            帳篷還在賣家家裡也可以先「新增預售」放上官網；送到店後在收購頁建寄售單時選「從預售帶入」。
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setEditingPresale(null)}>
          ＋新增預售
        </button>
      </div>

      {status.data && (
        <SyncStatus
          status={status.data}
          onPublish={() => publish.mutate()}
          publishing={publish.isPending}
        />
      )}

      {tents.isPending || presales.isPending ? (
        <p className="hint">讀取中…</p>
      ) : tents.isError || presales.isError ? (
        <p role="alert" className="form-error">
          {(tents.error ?? presales.error)?.message}
        </p>
      ) : rows.length === 0 && presaleRows.length === 0 ? (
        <p className="empty-state">
          目前沒有在庫的寄售品。寄售入庫後會出現在這裡；帳篷還沒送來可以先新增預售。
        </p>
      ) : (
        <div className="tents-table-wrap">
          <table className="data-table tents-table">
            <thead>
              <tr>
                <th>編號</th>
                <th>商品</th>
                <th>售價</th>
                <th>官網</th>
                <th>可以補上</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {presaleRows.map((presale) => {
                const gaps = missingInfo(presale);
                return (
                  <tr key={`presale-${presale.listing_id}`} className="tents-presale-row">
                    <td className="tents-code">
                      {presale.presale_code}
                      <span className="tents-badge tents-badge-presale">預售</span>
                    </td>
                    <td>
                      {fullName(presale)}
                      <span className="tents-seller">
                        賣家 {presale.seller_name}・{presale.seller_phone}
                      </span>
                    </td>
                    <td className="money">{money(presale.price)}</td>
                    <td>
                      <StateBadge tent={presale} />
                    </td>
                    <td className={gaps.length > 0 ? "tents-gaps" : "tents-gaps-none"}>
                      {gaps.length > 0 ? gaps.join("、") : "—"}
                    </td>
                    <td className="tents-actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        aria-label={`編輯 ${presale.presale_code}`}
                        onClick={() => setEditingPresale(presale)}
                      >
                        編輯
                      </button>
                      <button
                        type="button"
                        className="btn-ghost btn-danger-text"
                        aria-label={`取消預售 ${presale.presale_code}`}
                        onClick={() => setCancelling(presale)}
                      >
                        取消預售
                      </button>
                    </td>
                  </tr>
                );
              })}
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

      {editingPresale !== undefined && (
        <TentPresaleDialog
          presale={editingPresale}
          onClose={() => setEditingPresale(undefined)}
          onSaved={() => {
            setEditingPresale(undefined);
            void queryClient.invalidateQueries({ queryKey: PRESALES_KEY });
            publish.mutate();
          }}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: PRESALES_KEY })}
        />
      )}

      {cancelling !== null && (
        <ConfirmDialog
          title="取消這筆預售？"
          body={
            <>
              <p>
                {fullName(cancelling)}（{cancelling.presale_code}）會從官網拿掉。賣家不賣了、或帳篷沒送來時用。
              </p>
              {cancelPresale.isError && (
                <p role="alert" className="form-error">
                  {cancelPresale.error.message}
                </p>
              )}
            </>
          }
          confirmLabel="取消預售"
          cancelLabel="不要"
          danger
          busy={cancelPresale.isPending}
          onConfirm={() => cancelPresale.mutate(cancelling.listing_id)}
          onCancel={() => setCancelling(null)}
        />
      )}
    </section>
  );
}
