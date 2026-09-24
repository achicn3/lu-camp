"use client";
// /campaigns 門市活動管理頁（MANAGER 專用；docs/21、docs/40）。
// 清單（依 status 篩選）＋ 建立活動表單（含可疊加、指定商品範圍）＋ 啟用/結束/作廢操作。
// 純呈現：折扣/金額全由後端計算，前端只做「X 折」顯示轉換。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  offerDisplay,
  scopeSummary,
  statusLabel,
  targetSummary,
} from "@/features/campaigns/campaigns";
import { type PickedTarget, TargetPicker } from "@/features/campaigns/TargetPicker";
import { Pagination } from "@/features/common/Pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime, taipeiDateTimeLocalToUtc } from "@/lib/datetime";

type CampaignRead = components["schemas"]["CampaignRead"];
type CampaignStatus = components["schemas"]["CampaignStatus"];
type CampaignKind = components["schemas"]["CampaignKind"];

const KIND_OPTIONS: { value: CampaignKind; label: string }[] = [
  { value: "PERCENT_OFF", label: "打折" },
  { value: "FIXED_PRICE", label: "指定特價" },
  { value: "AMOUNT_OFF", label: "每件折金額" },
  { value: "BUY_N_GET_M", label: "買幾送幾" },
];

/** 買 N 送 M 的件數：1–99 的整數；不合法回 null。 */
function promoQty(value: string): number | null {
  if (!/^[1-9]\d?$/.test(value.trim())) return null;
  return parseInt(value, 10);
}

const PAGE_SIZE = 20;

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
  const [kind, setKind] = useState<CampaignKind>("PERCENT_OFF");
  const [discountPct, setDiscountPct] = useState("");
  const [moneyValue, setMoneyValue] = useState("");
  const [buyQty, setBuyQty] = useState("");
  const [freeQty, setFreeQty] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [appliesOwnedSerialized, setAppliesOwnedSerialized] = useState(true);
  const [appliesOwnedBulk, setAppliesOwnedBulk] = useState(true);
  const [appliesCatalog, setAppliesCatalog] = useState(false);
  const [appliesConsignment, setAppliesConsignment] = useState(false);
  const [stackable, setStackable] = useState(false);
  const [targets, setTargets] = useState<PickedTarget[]>([]);
  // 建立成功後換一個 key 讓範圍選擇器整個重來（清掉搜尋字與候選清單）。
  const [pickerKey, setPickerKey] = useState(0);
  const [lookupPending, setLookupPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      // 活動類型（docs/40 P2、P3）：打折填折扣 %；特價、折金額填含稅整數元；買幾送幾填件數。
      // 只送那一種的數值。
      let offer: {
        kind: CampaignKind;
        discount_pct?: number;
        fixed_price?: string;
        amount_off?: string;
        buy_qty?: number;
        free_qty?: number;
      };
      if (kind === "BUY_N_GET_M") {
        const buy = promoQty(buyQty);
        const free = promoQty(freeQty);
        if (buy === null || free === null) {
          throw new Error("買幾件、送幾件須為 1-99 的整數");
        }
        offer = { kind, buy_qty: buy, free_qty: free };
      } else if (kind === "PERCENT_OFF") {
        const pct = parseInt(discountPct, 10);
        if (isNaN(pct) || pct < 1 || pct > 99) {
          throw new Error("折扣 % 須為 1-99 的整數");
        }
        offer = { kind, discount_pct: pct };
      } else {
        if (!/^[1-9]\d*$/.test(moneyValue.trim())) {
          throw new Error(kind === "FIXED_PRICE" ? "特價須為大於 0 的整數元" : "折金額須為大於 0 的整數元");
        }
        offer =
          kind === "FIXED_PRICE"
            ? { kind, fixed_price: moneyValue.trim() }
            : { kind, amount_off: moneyValue.trim() };
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
          ...offer,
          starts_at: taipeiDateTimeLocalToUtc(startsAt),
          ends_at: taipeiDateTimeLocalToUtc(endsAt),
          applies_owned_serialized: appliesOwnedSerialized,
          applies_owned_bulk: appliesOwnedBulk,
          applies_catalog: appliesCatalog,
          // 寄售品不參加買 N 送 M（裁示 7）：切到買幾送幾時一律不送寄售。
          applies_consignment: kind === "BUY_N_GET_M" ? false : appliesConsignment,
          stackable,
          targets: targets.map(({ mode, target_type, target_id }) => ({
            mode,
            target_type,
            target_id,
          })),
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立活動失敗");
      return data;
    },
    onSuccess: () => {
      setFormError(null);
      setName("");
      setDiscountPct("");
      setMoneyValue("");
      setBuyQty("");
      setFreeQty("");
      setKind("PERCENT_OFF");
      setStartsAt("");
      setEndsAt("");
      setAppliesOwnedSerialized(true);
      setAppliesOwnedBulk(true);
      setAppliesCatalog(false);
      setAppliesConsignment(false);
      setStackable(false);
      setTargets([]);
      setPickerKey((k) => k + 1);
      // 舊選擇器被換掉後不會再回報查詢結束，這裡一併歸零，新表單才送得出去。
      setLookupPending(false);
      onCreated();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (lookupPending) return;
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

        <fieldset className="campaign-kind">
          <legend className="field-label">優惠方式</legend>
          {KIND_OPTIONS.map((o) => (
            <label key={o.value} className="campaign-checkbox">
              <input
                type="radio"
                name="campaign-kind"
                checked={kind === o.value}
                onChange={() => setKind(o.value)}
              />
              {o.label}
            </label>
          ))}
        </fieldset>

        {kind === "BUY_N_GET_M" ? (
          <div className="campaign-bngm">
            <label className="field">
              <span className="field-label">買幾件</span>
              <input
                inputMode="numeric"
                value={buyQty}
                onChange={(e) => setBuyQty(e.target.value)}
                placeholder="例如 5"
                required
              />
            </label>
            <label className="field">
              <span className="field-label">送幾件</span>
              <input
                inputMode="numeric"
                value={freeQty}
                onChange={(e) => setFreeQty(e.target.value)}
                placeholder="例如 1"
                required
              />
            </label>
            <p className="hint">
              符合的商品每湊滿「買＋送」件就成一組，送組內最便宜的；送的金額按價格比例分到整組每一件（退貨時退它分到的實付）。
            </p>
          </div>
        ) : kind === "PERCENT_OFF" ? (
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
        ) : (
          <label className="field">
            <span className="field-label">
              {kind === "FIXED_PRICE" ? "特價（含稅，元）" : "每件折多少（含稅，元）"}
            </span>
            <input
              inputMode="numeric"
              value={moneyValue}
              onChange={(e) => setMoneyValue(e.target.value)}
              placeholder={kind === "FIXED_PRICE" ? "例如 690" : "例如 100"}
              required
            />
          </label>
        )}

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
          一般商品
        </label>
        <p className="hint">餐飲（內用）品項一律不參與活動折扣，結帳時自動以原價計算。</p>
      </fieldset>

      {kind === "BUY_N_GET_M" ? (
        <p className="hint">寄售品不參加買幾送幾。</p>
      ) : (
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
      )}

      <TargetPicker
        key={pickerKey}
        targets={targets}
        onChange={setTargets}
        onLookupPendingChange={setLookupPending}
      />

      <fieldset className="campaign-scope-fieldset">
        <legend>與其他活動一起用</legend>
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            checked={stackable}
            onChange={(e) => setStackable(e.target.checked)}
          />
          可以和其他活動疊加
        </label>
        <p className="hint">
          可以同時進行多個活動。可疊加的活動會連乘（九折再九折＝81 折）；不可疊加的活動不會跟其他活動一起用。
          同一件商品符合好幾個活動時，系統自動挑對客人最划算的算法。
        </p>
      </fieldset>

      {formError !== null && (
        <p role="alert" className="form-error">{formError}</p>
      )}

      <button
        type="submit"
        className="btn-primary"
        disabled={create.isPending || lookupPending}
      >
        {create.isPending ? "建立中..." : lookupPending ? "查詢商品中…" : "建立活動"}
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
  const [page, setPage] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);

  // Access probe: use the list endpoint itself to detect 403/401.
  const listQuery = useQuery({
    queryKey: ["campaigns", statusFilter, page],
    queryFn: async () => {
      const params: { status?: CampaignStatus; limit: number; offset: number } = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
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

  // 總筆數（與清單同條件，key 帶 page 以便換頁時一併重抓——別台新增的資料
  // 若沒反映在總數，最後一頁會變成按不下去、那幾筆就看不到了）：算「第 X / Y 頁」，也避免整頁倍數時多出空白頁。
  // 權限不足時清單查詢已負責顯示提示，這裡安靜跳過即可。
  const totalQuery = useQuery({
    queryKey: ["campaigns", "count", statusFilter, page],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/campaigns/count", {
        params: { query: statusFilter === "ALL" ? {} : { status: statusFilter } },
      });
      if (!response.ok || !data) return undefined;
      return data.count;
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
              onChange={(e) => {
                setStatusFilter(e.target.value as CampaignStatus | "ALL");
                setPage(0);
              }}
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
                <th>優惠</th>
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
                  <td>
                    {offerDisplay(c)}
                    {c.stackable && <span className="row-sub">可疊加</span>}
                  </td>
                  <td>{formatTaipeiDateTime(c.starts_at)}</td>
                  <td>{formatTaipeiDateTime(c.ends_at)}</td>
                  <td>
                    <span className={`badge badge-${c.status.toLowerCase()}`}>
                      {statusLabel(c.status)}
                    </span>
                  </td>
                  <td>
                    {scopeSummary(c)}
                    {targetSummary(c.targets) && (
                      <span className="row-sub">{targetSummary(c.targets)}</span>
                    )}
                  </td>
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
        <Pagination
          page={page}
          count={campaigns.length}
          pageSize={PAGE_SIZE}
          total={totalQuery.isError ? undefined : totalQuery.data}
          onPage={setPage}
        />
      </div>
    </section>
  );
}
