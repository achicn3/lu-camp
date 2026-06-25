"use client";
// /campaigns 門市活動管理頁（MANAGER 專用；docs/21）。
// 清單（依 status 篩選）＋ 建立活動表單 ＋ 啟用/結束/作廢操作。
// 純呈現：折扣/金額全由後端計算，前端只做「X 折」顯示轉換。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  discountDisplay,
  scopeSummary,
  statusLabel,
} from "@/features/campaigns/campaigns";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type CampaignRead = components["schemas"]["CampaignRead"];
type CampaignStatus = components["schemas"]["CampaignStatus"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

// -- Create Campaign Form --

function CreateCampaignForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [discountPct, setDiscountPct] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [appliesOwnedSerialized, setAppliesOwnedSerialized] = useState(true);
  const [appliesOwnedBulk, setAppliesOwnedBulk] = useState(true);
  const [appliesCatalog, setAppliesCatalog] = useState(false);
  const [appliesConsignment, setAppliesConsignment] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const pct = parseInt(discountPct, 10);
      if (isNaN(pct) || pct < 1 || pct > 99) {
        throw new Error("折扣 % 須為 1-99 的整數");
      }
      if (!name.trim()) {
        throw new Error("請輸入活動名稱");
      }
      if (!startsAt || !endsAt) {
        throw new Error("請輸入開始與結束時間");
      }
      const { data, error } = await api.POST("/api/v1/campaigns", {
        body: {
          name: name.trim(),
          discount_pct: pct,
          starts_at: new Date(startsAt).toISOString(),
          ends_at: new Date(endsAt).toISOString(),
          applies_owned_serialized: appliesOwnedSerialized,
          applies_owned_bulk: appliesOwnedBulk,
          applies_catalog: appliesCatalog,
          applies_consignment: appliesConsignment,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立活動失敗");
      return data;
    },
    onSuccess: () => {
      setFormError(null);
      setName("");
      setDiscountPct("");
      setStartsAt("");
      setEndsAt("");
      setAppliesOwnedSerialized(true);
      setAppliesOwnedBulk(true);
      setAppliesCatalog(false);
      setAppliesConsignment(false);
      onCreated();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    create.mutate();
  }

  return (
    <form className="card campaign-form" onSubmit={handleSubmit}>
      <h2>建立活動</h2>

      <div className="campaign-form-grid">
        <label className="field">
          <span className="field-label">活動名稱</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：開幕九折"
            required
          />
        </label>

        <label className="field">
          <span className="field-label">折扣 %（1-99）</span>
          <input
            type="number"
            min={1}
            max={99}
            value={discountPct}
            onChange={(e) => setDiscountPct(e.target.value)}
            placeholder="10 = 打九折"
            required
          />
        </label>

        <label className="field">
          <span className="field-label">開始時間</span>
          <input
            type="datetime-local"
            value={startsAt}
            onChange={(e) => setStartsAt(e.target.value)}
            required
          />
        </label>

        <label className="field">
          <span className="field-label">結束時間</span>
          <input
            type="datetime-local"
            value={endsAt}
            onChange={(e) => setEndsAt(e.target.value)}
            required
          />
        </label>
      </div>

      <fieldset className="campaign-scope-fieldset">
        <legend>適用品項範圍</legend>
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            checked={appliesOwnedSerialized}
            onChange={(e) => setAppliesOwnedSerialized(e.target.checked)}
          />
          自有序號品
        </label>
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            checked={appliesOwnedBulk}
            onChange={(e) => setAppliesOwnedBulk(e.target.checked)}
          />
          自有散裝 (E 級)
        </label>
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            checked={appliesCatalog}
            onChange={(e) => setAppliesCatalog(e.target.checked)}
          />
          數量型商品
        </label>
        <p className="hint">餐飲（內用）品項一律不參與活動折扣，結帳時自動以原價計算。</p>
      </fieldset>

      <fieldset className="campaign-scope-fieldset">
        <legend>寄售品折扣</legend>
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            checked={appliesConsignment}
            onChange={(e) => setAppliesConsignment(e.target.checked)}
          />
          對寄售品套用折扣
        </label>
        {appliesConsignment && (
          <p className="hint">
            寄售品折扣一律按比例分攤：以折後價計算抽成與應付，寄售人也承擔折扣（不會由店家吸收）。
          </p>
        )}
      </fieldset>

      {formError !== null && (
        <p role="alert" className="form-error">{formError}</p>
      )}

      <button
        type="submit"
        className="btn-primary"
        disabled={create.isPending}
      >
        {create.isPending ? "建立中..." : "建立活動"}
      </button>
    </form>
  );
}

// -- Campaign Row Actions --

function CampaignActions({
  campaign,
  onAction,
}: {
  campaign: CampaignRead;
  onAction: (action: "activate" | "end" | "cancel", id: number) => void;
}) {
  return (
    <div className="campaign-actions">
      {campaign.status === "DRAFT" && (
        <button
          type="button"
          className="btn-ghost"
          onClick={() => onAction("activate", campaign.id)}
        >
          啟用
        </button>
      )}
      {campaign.status === "ACTIVE" && (
        <button
          type="button"
          className="btn-ghost"
          onClick={() => onAction("end", campaign.id)}
        >
          結束
        </button>
      )}
      {(campaign.status === "DRAFT" || campaign.status === "ACTIVE") && (
        <button
          type="button"
          className="btn-ghost btn-danger-text"
          onClick={() => onAction("cancel", campaign.id)}
        >
          作廢
        </button>
      )}
    </div>
  );
}

// -- Main Page --

const STATUS_FILTER_OPTIONS: { value: CampaignStatus | "ALL"; label: string }[] = [
  { value: "ALL", label: "全部" },
  { value: "DRAFT", label: "草稿" },
  { value: "ACTIVE", label: "生效中" },
  { value: "ENDED", label: "已結束" },
  { value: "CANCELLED", label: "已作廢" },
];

export default function CampaignsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<CampaignStatus | "ALL">("ALL");
  const [actionError, setActionError] = useState<string | null>(null);

  // Access probe: use the list endpoint itself to detect 403/401.
  const listQuery = useQuery({
    queryKey: ["campaigns", statusFilter],
    queryFn: async () => {
      const params: { status?: CampaignStatus } = {};
      if (statusFilter !== "ALL") {
        params.status = statusFilter;
      }
      const { data, error, response } = await api.GET("/api/v1/campaigns", {
        params: { query: params },
      });
      if (response.status === 401 || response.status === 403) {
        return { denied: true as const, campaigns: [] as CampaignRead[] };
      }
      if (response.ok && data) {
        return { denied: false as const, campaigns: data };
      }
      throw new Error(extractDetail(error) ?? "讀取活動清單失敗");
    },
    retry: false,
  });

  const actionMutation = useMutation({
    mutationFn: async ({ action, id }: { action: "activate" | "end" | "cancel"; id: number }) => {
      const endpoint = action === "activate"
        ? "/api/v1/campaigns/{campaign_id}/activate" as const
        : action === "end"
          ? "/api/v1/campaigns/{campaign_id}/end" as const
          : "/api/v1/campaigns/{campaign_id}/cancel" as const;
      const { data, error } = await api.POST(endpoint, {
        params: { path: { campaign_id: id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "操作失敗");
      return data;
    },
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (err: Error) => setActionError(err.message),
  });

  function handleAction(action: "activate" | "end" | "cancel", id: number) {
    setActionError(null);
    actionMutation.mutate({ action, id });
  }

  if (listQuery.isPending) {
    return (
      <section>
        <h1 className="page-title">門市活動</h1>
        <p className="hint">載入中...</p>
      </section>
    );
  }

  if (listQuery.isError) {
    return (
      <section>
        <h1 className="page-title">門市活動</h1>
        <p role="alert" className="form-error">{listQuery.error.message}</p>
      </section>
    );
  }

  if (listQuery.data.denied) {
    return (
      <section>
        <h1 className="page-title">門市活動</h1>
        <p>需管理者權限</p>
      </section>
    );
  }

  const campaigns = listQuery.data.campaigns;

  return (
    <section>
      <h1 className="page-title">門市活動</h1>

      <CreateCampaignForm
        onCreated={() => {
          void queryClient.invalidateQueries({ queryKey: ["campaigns"] });
        }}
      />

      <div className="campaign-list-section">
        <div className="rpt-filters">
          <label>
            狀態篩選
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as CampaignStatus | "ALL")}
            >
              {STATUS_FILTER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        </div>

        {actionError !== null && (
          <p role="alert" className="form-error">{actionError}</p>
        )}

        <div className="inv-table-wrap">
          <table className="inv-table">
            <thead>
              <tr>
                <th>名稱</th>
                <th>折扣</th>
                <th>開始</th>
                <th>結束</th>
                <th>狀態</th>
                <th>適用範圍</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>{discountDisplay(c.discount_pct)}</td>
                  <td>{new Date(c.starts_at).toLocaleString("zh-TW")}</td>
                  <td>{new Date(c.ends_at).toLocaleString("zh-TW")}</td>
                  <td>
                    <span className={`badge badge-${c.status.toLowerCase()}`}>
                      {statusLabel(c.status)}
                    </span>
                  </td>
                  <td>{scopeSummary(c)}</td>
                  <td>
                    <CampaignActions
                      campaign={c}
                      onAction={handleAction}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {campaigns.length === 0 && <p className="hint">尚無活動</p>}
        </div>
      </div>
    </section>
  );
}
