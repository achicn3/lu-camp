"use client";
// /stocktake 盤點（docs/10 §/stocktake，Phase 5）：建盤點單（快照現量）→ 逐項輸入實點數 →
// 即時顯示差異與彙總 → 確認調整（DRAFT→CONFIRMED，後端寫 ADJUST 帳，僅一次）。
// 全走 OpenAPI 生成型別 client（docs/11，禁手刻型別）；確認後不可改，前端只做清楚工作流。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  buildConfirmCounts,
  canConfirm,
  countError,
  parseCount,
  stStatusBadge,
  summarize,
  variance,
} from "@/features/stocktake/stocktake";
import { Pagination } from "@/features/common/Pagination";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

const PAGE_SIZE = 20;

type Stocktake = components["schemas"]["StocktakeRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function dt(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("zh-TW") : "—";
}

function VarianceCell({ value }: { value: number | null }) {
  if (value === null) return <span className="row-sub">未點</span>;
  if (value === 0) return <span className="st-var st-var-zero">0</span>;
  const tone = value > 0 ? "st-var-over" : "st-var-short";
  return (
    <span className={`st-var ${tone}`}>
      {value > 0 ? "+" : ""}
      {value}
    </span>
  );
}

// ── 盤點單明細：輸入實點數、確認 ──────────────────────────────
function StocktakeDetail({
  stocktakeId,
  productName,
  onClose,
}: {
  stocktakeId: number;
  productName: (id: number) => string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const detail = useQuery({
    queryKey: ["stocktake", stocktakeId],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/stocktakes/{stocktake_id}", {
        params: { path: { stocktake_id: stocktakeId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取盤點單失敗");
      return data;
    },
  });

  const confirm = useMutation({
    mutationFn: async (st: Stocktake) => {
      const entries = st.lines.map((line) => ({
        catalog_product_id: line.catalog_product_id,
        systemQty: line.system_qty,
        input: counts[line.catalog_product_id] ?? "",
      }));
      const { data, error } = await api.POST("/api/v1/stocktakes/{stocktake_id}/confirm", {
        params: { path: { stocktake_id: stocktakeId } },
        body: { counts: buildConfirmCounts(entries) },
      });
      if (!data) throw new Error(extractDetail(error) ?? "確認盤點失敗");
      return data;
    },
    onSuccess: () => {
      setConfirming(false);
      setConfirmError(null);
      void queryClient.invalidateQueries({ queryKey: ["stocktakes"] });
      void queryClient.invalidateQueries({ queryKey: ["stocktake", stocktakeId] });
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
    },
    onError: (err: Error) => setConfirmError(err.message),
  });

  if (detail.isPending) return <div className="card">載入中…</div>;
  if (detail.isError)
    return (
      <div className="card">
        <p role="alert" className="form-error">
          {detail.error.message}
        </p>
        <button type="button" className="btn-ghost" onClick={onClose}>
          返回清單
        </button>
      </div>
    );

  const st = detail.data;
  const draft = st.status === "DRAFT";
  const entries = st.lines.map((line) => ({
    systemQty: line.system_qty,
    input: draft ? (counts[line.catalog_product_id] ?? "") : String(line.counted_qty ?? ""),
  }));
  const summary = summarize(entries);
  const confirmable = draft && canConfirm("DRAFT", entries);

  return (
    <div className="card st-detail">
      <div className="st-detail-head">
        <h2>
          盤點單 #{st.id}{" "}
          <span className={`inv-badge inv-tone-${stStatusBadge(st.status).tone}`}>
            {stStatusBadge(st.status).label}
          </span>
        </h2>
        <button type="button" className="btn-ghost" onClick={onClose}>
          返回清單
        </button>
      </div>
      <p className="hint">
        建立 {dt(st.created_at)}
        {st.confirmed_at && ` ・ 確認 ${dt(st.confirmed_at)}`}
      </p>

      <div className="st-summary">
        <span>
          已點 <strong>{summary.counted}</strong> / 未點 <strong>{summary.uncounted}</strong>
        </span>
        <span className="st-var-over">盤盈 +{summary.over}</span>
        <span className="st-var-short">盤虧 −{summary.short}</span>
        <span>
          淨差異 <strong>{summary.net > 0 ? `+${summary.net}` : summary.net}</strong>
        </span>
      </div>

      <table className="data-table st-lines">
        <thead>
          <tr>
            <th>商品</th>
            <th>系統現量</th>
            <th>實點數</th>
            <th>差異</th>
          </tr>
        </thead>
        <tbody>
          {st.lines.map((line) => {
            const raw = draft
              ? (counts[line.catalog_product_id] ?? "")
              : String(line.counted_qty ?? "");
            const err = countError(raw);
            const v = draft ? variance(line.system_qty, parseCount(raw)) : line.variance;
            return (
              <tr key={line.id}>
                <td>{productName(line.catalog_product_id)}</td>
                <td>{line.system_qty}</td>
                <td>
                  {draft ? (
                    <input
                      inputMode="numeric"
                      className={`st-count ${err ? "input-error" : ""}`}
                      aria-label={`實點數 ${productName(line.catalog_product_id)}`}
                      aria-invalid={err !== null}
                      value={raw}
                      onChange={(e) =>
                        setCounts((prev) => ({
                          ...prev,
                          [line.catalog_product_id]: e.target.value,
                        }))
                      }
                    />
                  ) : (
                    (line.counted_qty ?? "—")
                  )}
                </td>
                <td>
                  <VarianceCell value={v} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {draft && (
        <>
          {confirmError !== null && (
            <p role="alert" className="form-error">
              {confirmError}
            </p>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={!confirmable || confirm.isPending}
            onClick={() => {
              setConfirming(true);
              setConfirmError(null);
            }}
          >
            確認盤點調整
          </button>
        </>
      )}

      {confirming && (
        <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="確認盤點">
          <div className="card pos-dialog">
            <h2>確認盤點調整</h2>
            <p className="hint">
              將依實點數校正現量並寫入庫存調整紀錄（盤盈 +{summary.over}、盤虧 −{summary.short}）。
              此操作僅能執行一次且無法復原。
            </p>
            {confirmError !== null && (
              <p role="alert" className="form-error">
                {confirmError}
              </p>
            )}
            <div className="pos-dialog-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={confirm.isPending}
                onClick={() => confirm.mutate(st)}
              >
                {confirm.isPending ? "確認中…" : "確認調整"}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={confirm.isPending}
                onClick={() => setConfirming(false)}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function StocktakePage() {
  const queryClient = useQueryClient();
  const [openId, setOpenId] = useState<number | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const catalog = useQuery({
    queryKey: ["catalog-products", "all"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/catalog-products", {
        params: { query: { limit: 200, offset: 0 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取商品失敗");
      return data;
    },
  });

  const productName = useMemo(() => {
    const map = new Map((catalog.data ?? []).map((p) => [p.id, `${p.name}（${p.sku}）`] as const));
    return (id: number) => map.get(id) ?? `#${id}`;
  }, [catalog.data]);

  const stocktakes = useQuery({
    queryKey: ["stocktakes", page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/stocktakes", {
        params: { query: { limit: PAGE_SIZE, offset: page * PAGE_SIZE } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取盤點單失敗");
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/stocktakes", {});
      if (!data) throw new Error(extractDetail(error) ?? "建立盤點單失敗");
      return data;
    },
    onSuccess: (st) => {
      setCreateError(null);
      void queryClient.invalidateQueries({ queryKey: ["stocktakes"] });
      setOpenId(st.id);
    },
    onError: (err: Error) => setCreateError(err.message),
  });

  const rows = stocktakes.data ?? [];

  return (
    <section className="st-page">
      <div className="st-head">
        <div>
          <h1 className="page-title">盤點</h1>
          <p className="hint">建立盤點單會快照目前所有數量品的系統現量，逐項點數後確認調整。</p>
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "建立中…" : "建立盤點單"}
        </button>
      </div>

      {createError !== null && (
        <p role="alert" className="form-error">
          {createError}
        </p>
      )}

      {openId !== null ? (
        <StocktakeDetail
          stocktakeId={openId}
          productName={productName}
          onClose={() => setOpenId(null)}
        />
      ) : (
        <div className="card st-list">
          <h2>盤點單清單</h2>
          {stocktakes.isPending ? (
            <p>載入中…</p>
          ) : stocktakes.isError ? (
            <p role="alert" className="form-error">
              {stocktakes.error.message}
            </p>
          ) : rows.length === 0 ? (
            <p className="empty-state">尚無盤點單。點「建立盤點單」開始。</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>單號</th>
                  <th>狀態</th>
                  <th>建立時間</th>
                  <th>確認時間</th>
                  <th>項數</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((st) => {
                  const badge = stStatusBadge(st.status);
                  return (
                    <tr key={st.id}>
                      <td>#{st.id}</td>
                      <td>
                        <span className={`inv-badge inv-tone-${badge.tone}`}>{badge.label}</span>
                      </td>
                      <td>{dt(st.created_at)}</td>
                      <td>{dt(st.confirmed_at)}</td>
                      <td>{st.lines.length}</td>
                      <td>
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => setOpenId(st.id)}
                        >
                          {st.status === "DRAFT" ? "盤點" : "檢視"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {!stocktakes.isPending && !stocktakes.isError && (
            <Pagination page={page} count={rows.length} pageSize={PAGE_SIZE} onPage={setPage} />
          )}
        </div>
      )}
    </section>
  );
}
