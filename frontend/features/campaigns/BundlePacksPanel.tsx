"use client";
// 組合包袋裝條碼（ADR-028；店主 2026-10-04）：在組合價活動底下建「一袋」——指定袋裡實際裝了什麼、
// 印條碼貼在袋子上。POS 掃這張條碼就把袋裡每件商品加進購物車，價錢照這個組合價活動算。
// 袋裡內容要剛好湊成一組，由後端用結帳同一支計價引擎判斷（建立時不合就說明原因）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  type PackCandidate,
  packCandidateKey,
  packCandidates,
  packItems,
  packUnitsRequired,
} from "@/features/campaigns/packs";
import { printLabel } from "@/lib/agent";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Campaign = components["schemas"]["CampaignRead"];
type Pack = components["schemas"]["BundlePackRead"];

const ITEM_TYPE_LABEL: Record<components["schemas"]["BundlePackItemType"], string> = {
  CATALOG: "一般商品",
  BULK_BASKET: "散裝販售籃",
  SERIALIZED: "單件",
};

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 依條碼找商品（序號品 item_code → 販售籃 K… → 一般商品 SKU），給「依分類／品牌指定」的格子用。 */
async function lookupByCode(code: string): Promise<PackCandidate> {
  const serialized = await api.GET("/api/v1/serialized-items/by-code/{item_code}", {
    params: { path: { item_code: code } },
  });
  if (serialized.data) {
    const item = serialized.data;
    return {
      key: packCandidateKey("SERIALIZED", item.id),
      item_type: "SERIALIZED",
      target_id: item.id,
      label: `${item.name}（${item.item_code}）`,
    };
  }
  const basket = await api.GET("/api/v1/bulk-baskets/by-code/{code}", {
    params: { path: { code } },
  });
  if (basket.data) {
    return {
      key: packCandidateKey("BULK_BASKET", basket.data.id),
      item_type: "BULK_BASKET",
      target_id: basket.data.id,
      label: basket.data.name,
    };
  }
  const catalog = await api.GET("/api/v1/catalog-products/by-sku/{sku}", {
    params: { path: { sku: code } },
  });
  if (catalog.data) {
    return {
      key: packCandidateKey("CATALOG", catalog.data.id),
      item_type: "CATALOG",
      target_id: catalog.data.id,
      label: catalog.data.name,
    };
  }
  throw new Error(`找不到此條碼：${code}`);
}

function CreatePackForm({ campaign, onCreated }: { campaign: Campaign; onCreated: () => void }) {
  const slots = campaign.bundle_slots ?? [];
  const { items: slotItems, broadTargets } = packCandidates(slots);
  const [extra, setExtra] = useState<PackCandidate[]>([]);
  const candidates = [...slotItems, ...extra.filter((e) => !slotItems.some((s) => s.key === e.key))];
  const [qty, setQty] = useState<Record<string, string>>({});
  const [name, setName] = useState(campaign.name);
  const [scanCode, setScanCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const required = packUnitsRequired(slots);
  const chosen = packItems(candidates, qty);
  const placed = chosen.reduce((sum, item) => sum + item.qty, 0);

  const lookup = useMutation({
    mutationFn: lookupByCode,
    onSuccess: (candidate) => {
      setExtra((prev) => (prev.some((c) => c.key === candidate.key) ? prev : [...prev, candidate]));
      if (candidate.item_type === "SERIALIZED") setQty((prev) => ({ ...prev, [candidate.key]: "1" }));
      setScanCode("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr } = await api.POST("/api/v1/campaigns/{campaign_id}/packs", {
        params: { path: { campaign_id: campaign.id } },
        body: { name: name.trim(), items: chosen },
      });
      if (!data) throw new Error(detail(apiErr) ?? "建立失敗");
      return data;
    },
    onSuccess: () => {
      setQty({});
      setExtra([]);
      setError(null);
      onCreated();
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setError("請填袋子名稱（印在標籤上）");
    if (chosen.length === 0) return setError("請填袋裡每樣商品放幾件");
    create.mutate();
  }

  return (
    <form className="bundle-pack-form" aria-label="建立袋裝條碼" onSubmit={submit}>
      <label className="field">
        <span className="field-label">袋子名稱（印在標籤上）</span>
        <input aria-label="袋子名稱" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
      </label>
      <p className="hint">
        這個組合一組共 {required} 件；填這一袋實際放了哪些、各幾件（已放 {placed}／{required} 件）。
      </p>
      <ul className="bundle-pack-candidates">
        {candidates.map((candidate) => (
          <li key={candidate.key}>
            <span>
              {candidate.label}
              <span className="row-sub">{ITEM_TYPE_LABEL[candidate.item_type]}</span>
            </span>
            {candidate.item_type === "SERIALIZED" ? (
              <label className="bundle-pack-check">
                <input
                  type="checkbox"
                  aria-label={`放入 ${candidate.label}`}
                  checked={qty[candidate.key] === "1"}
                  onChange={(e) => setQty((prev) => ({ ...prev, [candidate.key]: e.target.checked ? "1" : "" }))}
                />
                放入
              </label>
            ) : (
              <input
                aria-label={`${candidate.label} 件數`}
                inputMode="numeric"
                className="bundle-pack-qty"
                value={qty[candidate.key] ?? ""}
                placeholder="件數"
                onChange={(e) => setQty((prev) => ({ ...prev, [candidate.key]: e.target.value }))}
              />
            )}
          </li>
        ))}
      </ul>
      <div className="bundle-pack-scan">
        <label className="field">
          <span className="field-label">
            {broadTargets.length > 0
              ? `用條碼加商品（${broadTargets.join("、")} 這類依分類／品牌指定的）`
              : "用條碼加商品"}
          </span>
          <input
            aria-label="用條碼加商品"
            value={scanCode}
            placeholder="掃商品條碼後按 Enter"
            onChange={(e) => setScanCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              if (scanCode.trim()) lookup.mutate(scanCode.trim());
            }}
          />
        </label>
      </div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={create.isPending}>
        {create.isPending ? "建立中…" : "建立袋裝條碼"}
      </button>
    </form>
  );
}

function PackRow({ pack, bundlePrice, onChanged }: { pack: Pack; bundlePrice: number; onChanged: () => void }) {
  const print = useMutation({ mutationFn: () => printLabel(pack.code, pack.name, bundlePrice) });
  const deactivate = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/bundle-packs/{pack_id}/deactivate", {
        params: { path: { pack_id: pack.id } },
      });
      if (!data) throw new Error(detail(error) ?? "停用失敗");
      return data;
    },
    onSuccess: onChanged,
  });
  const problem = print.error ?? deactivate.error;
  return (
    <tr>
      <td>
        <span className="money">{pack.code}</span>
        <span className="row-sub">{pack.name}</span>
      </td>
      <td>{pack.items.map((item) => `${item.label}×${item.qty}`).join("、")}</td>
      <td>{pack.is_active ? "使用中" : "已停用"}</td>
      <td>
        {pack.is_active && (
          <div className="campaign-actions">
            <button type="button" className="btn-ghost" disabled={print.isPending} onClick={() => print.mutate()}>
              {print.isPending ? "列印中…" : print.isSuccess ? "再印一張" : "印標籤"}
            </button>
            <button
              type="button"
              className="btn-ghost btn-danger-text"
              disabled={deactivate.isPending}
              onClick={() => {
                if (window.confirm(`停用「${pack.name}」？停用後這張條碼掃不到。`)) deactivate.mutate();
              }}
            >
              停用
            </button>
          </div>
        )}
        {print.isSuccess && <span className="row-sub">已送出列印</span>}
        {problem && (
          <span role="alert" className="form-error">
            {problem.message}
          </span>
        )}
      </td>
    </tr>
  );
}

export function BundlePacksPanel({ campaign, onClose }: { campaign: Campaign; onClose: () => void }) {
  const queryClient = useQueryClient();
  const queryKey = ["bundle-packs", campaign.id];
  const packs = useQuery({
    queryKey,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/campaigns/{campaign_id}/packs", {
        params: { path: { campaign_id: campaign.id } },
      });
      if (!data) throw new Error(detail(error) ?? "讀取袋裝條碼失敗");
      return data;
    },
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey });
  const bundlePrice = parseNtd(campaign.bundle_price ?? "0") ?? 0;
  const open = campaign.status === "DRAFT" || campaign.status === "ACTIVE";

  return (
    <section className="card bundle-packs" aria-label={`${campaign.name} 袋裝條碼`}>
      <div className="bundle-packs-head">
        <h2>
          袋裝條碼：{campaign.name}（組合價 ${formatNtd(bundlePrice)}）
        </h2>
        <button type="button" className="btn-ghost" onClick={onClose}>
          關閉
        </button>
      </div>
      <p className="hint">
        先把商品包成一袋，再在這裡填袋裡放了什麼、印條碼貼上。POS 掃這張條碼會把袋裡每件商品加進購物車，
        價錢照這個組合價算、每件各自扣庫存。包袋時不會先扣庫存；袋裡的二手單件若被另外賣掉，這袋就掃不了。
      </p>
      {open ? (
        <CreatePackForm campaign={campaign} onCreated={refresh} />
      ) : (
        <p className="hint">活動已結束或作廢，不能再建新的袋子。</p>
      )}
      {packs.isError && (
        <p role="alert" className="form-error">
          {packs.error.message}
        </p>
      )}
      {(packs.data?.length ?? 0) > 0 && (
        <div className="inv-table-wrap">
          <table className="inv-table">
            <thead>
              <tr>
                <th>條碼</th>
                <th>袋裡</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {packs.data?.map((pack) => (
                <PackRow key={pack.id} pack={pack} bundlePrice={bundlePrice} onChanged={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
