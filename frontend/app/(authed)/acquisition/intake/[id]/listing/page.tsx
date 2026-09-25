"use client";
// /acquisition/intake/[id]/listing 一批的待整理上架（docs/42 §8）：付款時已建好的商品，
// 補品名／成色／品牌型號／分類／售價，勾幾件上幾件，上架後印標籤。成本與件數客人簽過，不能改。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { type ComboOption, CreatableCombobox } from "@/features/acquisition/CreatableCombobox";
import { GRADE_LABEL, SERIALIZED_GRADES } from "@/features/acquisition/labels";
import {
  type Draft,
  draftFrom,
  editFor,
  missingOf,
  printListedLabels,
} from "@/features/intake/listing";
import { useIntakeReceiptPrint } from "@/features/intake/receipt";
import { StatusBadge } from "@/features/intake/StatusBadge";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Item = components["schemas"]["IntakeItemRead"];
type Edit = components["schemas"]["IntakeItemEdit"];
type Grade = components["schemas"]["Grade"];

const keyOf = (item: Item) => `${item.kind}:${item.id}`;

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function money(value: string | null | undefined): string {
  const n = value == null ? null : parseNtd(value);
  return n === null ? "—" : `$${formatNtd(n)}`;
}

function searchBrands(q: string): Promise<ComboOption[]> {
  return api
    .GET("/api/v1/brands", { params: { query: { q } } })
    .then(({ data }) => (data ?? []).map((b) => ({ id: b.id, name: b.name })));
}

function createBrand(name: string): Promise<ComboOption> {
  return api.POST("/api/v1/brands", { body: { name } }).then(({ data, error }) => {
    if (!data) throw new Error(detail(error) ?? "建立品牌失敗");
    return { id: data.id, name: data.name };
  });
}

function ItemCard({
  item,
  draft,
  checked,
  categories,
  onCheck,
  onChange,
  onCategoryCreated,
}: {
  item: Item;
  draft: Draft;
  checked: boolean;
  categories: ComboOption[];
  onCheck: (checked: boolean) => void;
  onChange: (patch: Partial<Draft>) => void;
  onCategoryCreated: () => void;
}) {
  const serialized = item.kind === "SERIALIZED";
  const missing = missingOf(draft);
  return (
    <div className={`card intake-list-item${checked ? " is-checked" : ""}`}>
      <div className="intake-list-item-head">
        <label className="campaign-checkbox">
          <input
            type="checkbox"
            aria-label={`勾選 ${item.code}`}
            checked={checked}
            onChange={(e) => onCheck(e.target.checked)}
          />
          <strong>{serialized ? "二手商品" : `散裝 ×${item.qty}`}</strong>
          <span className="hint">{item.code}</span>
        </label>
        <span className="hint">
          {item.consignment ? "寄售" : `成本 ${money(item.acquisition_cost)}${serialized ? "" : "／件"}`}
        </span>
        {missing.includes("分類") ? (
          <span className="intake-over">缺分類（上架必填）</span>
        ) : missing.length > 0 ? (
          <span className="hint">建議補：{missing.join("、")}</span>
        ) : (
          <span className="form-success">資料齊了</span>
        )}
      </div>
      <div className={`intake-list-grid${serialized ? "" : " is-bulk"}`}>
        {!serialized && (
          <label className="field intake-list-name">
            <span className="field-label">品名</span>
            <input
              aria-label={`${item.code} 品名`}
              value={draft.name}
              maxLength={150}
              onChange={(e) => onChange({ name: e.target.value })}
            />
          </label>
        )}
        <CreatableCombobox
          label="品牌"
          search={searchBrands}
          create={createBrand}
          placeholder="選擇或新增品牌"
          selectedId={draft.brandId}
          selectedName={draft.brandName}
          onChange={(o) =>
            onChange({ brandId: o?.id ?? null, brandName: o?.name ?? null, modelId: null, modelName: null })
          }
        />
        {serialized && (
          <CreatableCombobox
            label="型號"
            search={(q) =>
              api
                .GET("/api/v1/product-models", {
                  params: { query: { q, brand_id: draft.brandId ?? undefined } },
                })
                .then(({ data }) => (data ?? []).map((m) => ({ id: m.id, name: m.name })))
            }
            create={(name) => {
              if (draft.brandId === null) return Promise.reject(new Error("請先選擇品牌"));
              return api
                .POST("/api/v1/product-models", { body: { brand_id: draft.brandId, name } })
                .then(({ data, error }) => {
                  if (!data) throw new Error(detail(error) ?? "建立型號失敗");
                  return { id: data.id, name: data.name };
                });
            }}
            placeholder={draft.brandId === null ? "先選品牌" : "選擇或新增型號"}
            disabled={draft.brandId === null}
            selectedId={draft.modelId}
            selectedName={draft.modelName}
            // 同收購頁：選了型號，品名就用型號（要改再展開品名）。
            onChange={(o) =>
              onChange({
                modelId: o?.id ?? null,
                modelName: o?.name ?? null,
                ...(o ? { name: o.name } : {}),
              })
            }
          />
        )}
        <CreatableCombobox
          label="分類"
          search={(q) =>
            Promise.resolve(categories.filter((c) => c.name.toLowerCase().includes(q.toLowerCase())))
          }
          create={(name) =>
            api.POST("/api/v1/categories", { body: { name } }).then(({ data, error }) => {
              if (!data) throw new Error(detail(error) ?? "建立分類失敗");
              onCategoryCreated();
              return { id: data.id, name: data.name };
            })
          }
          placeholder="選擇或新增分類"
          selectedId={draft.categoryId}
          selectedName={draft.categoryName}
          onChange={(o) => onChange({ categoryId: o?.id ?? null, categoryName: o?.name ?? null })}
        />
        {serialized && (
          <label className="field">
            <span className="field-label">成色</span>
            <select
              aria-label={`${item.code} 成色`}
              value={draft.grade}
              onChange={(e) => onChange({ grade: e.target.value as Grade })}
            >
              {SERIALIZED_GRADES.map((g) => (
                <option key={g} value={g}>
                  {GRADE_LABEL[g]}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span className="field-label">{serialized ? "售價" : "每件售價"}</span>
          <input
            aria-label={`${item.code} 售價`}
            inputMode="numeric"
            value={draft.price}
            onChange={(e) => onChange({ price: e.target.value })}
          />
        </label>
        {serialized && (
          <details className="acq-name-detail intake-list-name">
            <summary>品名：{draft.name || "選型號自動帶入，或展開填寫"}</summary>
            <input
              aria-label={`${item.code} 品名`}
              value={draft.name}
              maxLength={150}
              onChange={(e) => onChange({ name: e.target.value })}
            />
          </details>
        )}
        <label className="field intake-list-note">
          <span className="field-label">備註（選填）</span>
          <input
            aria-label={`${item.code} 備註`}
            value={draft.note}
            maxLength={500}
            onChange={(e) => onChange({ note: e.target.value })}
          />
        </label>
      </div>
    </div>
  );
}

export default function IntakeListingPage() {
  const params = useParams<{ id: string }>();
  const batchId = Number(params.id);
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [order, setOrder] = useState<string[] | null>(null);
  const [bulkCategory, setBulkCategory] = useState<ComboOption | null>(null);
  const [bulkGrade, setBulkGrade] = useState<Grade | "">("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastListed, setLastListed] = useState<Item[]>([]);
  const { print: printReceipt, note: receiptNote } = useIntakeReceiptPrint(batchId);

  const batch = useQuery({
    queryKey: ["intake-batch", batchId],
    queryFn: async () => {
      const { data, error: apiErr } = await api.GET("/api/v1/intake-batches/{batch_id}", {
        params: { path: { batch_id: batchId } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "讀取失敗");
      return data;
    },
  });
  const items = useQuery({
    queryKey: ["intake-items", batchId],
    queryFn: async () => {
      const { data, error: apiErr } = await api.GET("/api/v1/intake-batches/{batch_id}/items", {
        params: { path: { batch_id: batchId } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "讀取商品失敗");
      return data;
    },
  });
  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: async () =>
      (await api.GET("/api/v1/categories", { params: { query: { limit: 200 } } })).data ?? [],
  });
  const categories = (categoriesQuery.data ?? []).map((c) => ({ id: c.id, name: c.name }));

  const pending = useMemo(() => (items.data ?? []).filter((i) => !i.listed), [items.data]);
  const listed = useMemo(() => (items.data ?? []).filter((i) => i.listed), [items.data]);

  // 第一次載入時：缺分類的排前面、預設全勾。之後不再重排——邊填邊跳位置找不到剛剛那件。
  if (items.data && order === null) {
    const sorted = [...pending].sort(
      (a, b) => Number(b.missing.includes("分類")) - Number(a.missing.includes("分類")),
    );
    setOrder(sorted.map(keyOf));
    setChecked(Object.fromEntries(pending.map((i) => [keyOf(i), true])));
  }
  const draftOf = (item: Item) => drafts[keyOf(item)] ?? draftFrom(item);
  const pendingInOrder = (order ?? [])
    .map((key) => pending.find((i) => keyOf(i) === key))
    .filter((i): i is Item => i !== undefined);
  const selected = pendingInOrder.filter((i) => checked[keyOf(i)]);

  function patch(item: Item, change: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [keyOf(item)]: { ...draftOf(item), ...change } }));
  }

  function applyToSelected() {
    setDrafts((prev) => {
      const next = { ...prev };
      for (const item of selected) {
        const draft = { ...(next[keyOf(item)] ?? draftFrom(item)) };
        if (bulkCategory) {
          draft.categoryId = bulkCategory.id;
          draft.categoryName = bulkCategory.name;
        }
        if (bulkGrade !== "" && item.kind === "SERIALIZED") draft.grade = bulkGrade;
        next[keyOf(item)] = draft;
      }
      return next;
    });
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["intake-items", batchId] });
    void queryClient.invalidateQueries({ queryKey: ["intake-batch", batchId] });
    void queryClient.invalidateQueries({ queryKey: ["intake-awaiting-listing"] });
  }

  async function send(edits: Edit[], publish: boolean) {
    const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/listing", {
      params: { path: { batch_id: batchId } },
      body: { items: edits, publish },
    });
    if (!data) throw new Error(detail(apiErr) ?? "儲存失敗");
    return data;
  }

  const save = useMutation({
    mutationFn: async () => {
      const edits = pending.map((i) => editFor(i, draftOf(i))).filter((e): e is Edit => e !== null);
      if (edits.length === 0) return 0;
      await send(edits, false);
      return edits.length;
    },
    onSuccess: (count) => {
      setError(null);
      setNotice(count === 0 ? "沒有要儲存的修改。" : `已儲存 ${count} 件的資料（還沒上架）。`);
      setDrafts({});
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const publish = useMutation({
    mutationFn: async () => {
      if (selected.length === 0) throw new Error("請先勾選要上架的商品");
      const noCategory = selected.filter((i) => draftOf(i).categoryId === null);
      if (noCategory.length > 0) {
        throw new Error(`還有 ${noCategory.length} 件沒選分類，上架前要選好`);
      }
      // 沒勾的也有改過就先存，免得上架完重新整理把店員填的資料洗掉。
      const others = pending
        .filter((i) => !checked[keyOf(i)])
        .map((i) => editFor(i, draftOf(i)))
        .filter((e): e is Edit => e !== null);
      if (others.length > 0) await send(others, false);
      const edits = selected.map(
        (i) => editFor(i, draftOf(i)) ?? { kind: i.kind === "BULK_LOT" ? "BULK_LOT" : "SERIALIZED", id: i.id },
      ) as Edit[];
      return (await send(edits, true)).listed;
    },
    onSuccess: async (listedNow) => {
      setError(null);
      setDrafts({});
      setLastListed(listedNow);
      refresh();
      try {
        const count = await printListedLabels(listedNow);
        setNotice(`已上架 ${listedNow.length} 件，${count} 張標籤已送出列印。`);
      } catch (e) {
        setNotice(
          `已上架 ${listedNow.length} 件，但標籤沒有印出來：${(e as Error).message}。請按「重印這次的標籤」。`,
        );
      }
    },
    onError: (e: Error) => setError(e.message),
  });

  const reprint = useMutation({
    mutationFn: () => printListedLabels(lastListed),
    onSuccess: (count) => setNotice(`${count} 張標籤已送出列印。`),
    onError: (e: Error) => setNotice(`標籤沒有印出來：${e.message}`),
  });

  if (batch.isError || items.isError) {
    return (
      <section className="intake-page">
        <p role="alert" className="form-error">
          {(items.error ?? batch.error)?.message ?? "讀取失敗"}
        </p>
        <Link href="/acquisition/intake/listing">回待整理清單</Link>
      </section>
    );
  }
  if (!batch.data || !items.data) return <p className="hint">讀取中…</p>;
  const b = batch.data;
  const pendingPieces = pending.reduce((sum, i) => sum + i.qty, 0);
  const listedPieces = listed.reduce((sum, i) => sum + i.qty, 0);

  return (
    <section className="intake-page">
      <div className="pur-page-head">
        <h1 className="page-title">
          <span className="intake-ticket-big">{b.ticket_label}</span> {b.contact_name}・整理上架
        </h1>
        <Link href="/acquisition/intake/listing" className="btn-ghost">
          回待整理清單
        </Link>
      </div>
      <div className="card intake-summary">
        <span>
          狀態：<StatusBadge status={b.status} />
        </span>
        {b.paid_at && <span>付款 {formatTaipeiDateTime(b.paid_at, { omitYear: true })}</span>}
        <span>
          已上架 {listedPieces} 件・待整理 {pendingPieces} 件
        </span>
        <span className="hint">成本與件數是客人簽過的，這裡不能改；要更正請作廢收購。</span>
      </div>
      {(b.signature_task_id !== null || receiptNote !== null) && (
        <div className="intake-print">
          {b.signature_task_id !== null && (
            <button
              type="button"
              className="btn-secondary"
              disabled={printReceipt.isPending}
              onClick={() => printReceipt.mutate()}
            >
              列印收購明細（含簽名）
            </button>
          )}
          {receiptNote !== null && (
            <span role="status" className="hint">
              {receiptNote}
            </span>
          )}
        </div>
      )}

      {pendingInOrder.length > 0 ? (
        <>
          <div className="card intake-bulk-apply" aria-label="整批套用">
            <strong>整批套用到勾選的 {selected.length} 件</strong>
            <CreatableCombobox
              label="分類"
              search={(q) =>
                Promise.resolve(categories.filter((c) => c.name.toLowerCase().includes(q.toLowerCase())))
              }
              create={(name) =>
                api.POST("/api/v1/categories", { body: { name } }).then(({ data, error: apiErr }) => {
                  if (!data) throw new Error(detail(apiErr) ?? "建立分類失敗");
                  void categoriesQuery.refetch();
                  return { id: data.id, name: data.name };
                })
              }
              placeholder="選擇或新增分類"
              selectedId={bulkCategory?.id ?? null}
              selectedName={bulkCategory?.name ?? null}
              onChange={setBulkCategory}
            />
            <label className="field">
              <span className="field-label">成色</span>
              <select
                aria-label="整批成色"
                value={bulkGrade}
                onChange={(e) => setBulkGrade(e.target.value as Grade | "")}
              >
                <option value="">不改</option>
                {SERIALIZED_GRADES.map((g) => (
                  <option key={g} value={g}>
                    {GRADE_LABEL[g]}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn-secondary"
              disabled={selected.length === 0 || (bulkCategory === null && bulkGrade === "")}
              onClick={applyToSelected}
            >
              套用
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() =>
                setChecked(
                  Object.fromEntries(
                    pendingInOrder.map((i) => [keyOf(i), selected.length !== pendingInOrder.length]),
                  ),
                )
              }
            >
              {selected.length === pendingInOrder.length ? "全部取消勾選" : "全部勾選"}
            </button>
          </div>

          {pendingInOrder.map((item) => (
            <ItemCard
              key={keyOf(item)}
              item={item}
              draft={draftOf(item)}
              checked={checked[keyOf(item)] ?? false}
              categories={categories}
              onCheck={(value) => setChecked((prev) => ({ ...prev, [keyOf(item)]: value }))}
              onChange={(change) => patch(item, change)}
              onCategoryCreated={() => void categoriesQuery.refetch()}
            />
          ))}
        </>
      ) : (
        <p className="form-success card">這一批都上架完了。</p>
      )}

      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {notice !== null && (
        <p role="status" className="form-success">
          {notice}
        </p>
      )}
      <div className="intake-footer">
        {pendingInOrder.length > 0 && (
          <>
            <button
              type="button"
              className="btn-primary intake-pay-button"
              disabled={publish.isPending || selected.length === 0}
              onClick={() => publish.mutate()}
            >
              {publish.isPending ? "上架中…" : `上架勾選的 ${selected.length} 件並印標籤`}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={save.isPending}
              onClick={() => save.mutate()}
            >
              儲存（先不上架）
            </button>
          </>
        )}
        {lastListed.length > 0 && (
          <button
            type="button"
            className="btn-ghost"
            disabled={reprint.isPending}
            onClick={() => reprint.mutate()}
          >
            重印這次的標籤（{lastListed.length} 張）
          </button>
        )}
      </div>

      {listed.length > 0 && (
        <div className="card">
          <h2>已上架</h2>
          <div className="intake-table-scroll">
            <table className="intake-lines">
              <thead>
                <tr>
                  <th scope="col">條碼</th>
                  <th scope="col">品名</th>
                  <th scope="col">品牌</th>
                  <th scope="col">分類</th>
                  <th scope="col">數量</th>
                  <th scope="col">售價</th>
                </tr>
              </thead>
              <tbody>
                {listed.map((item) => (
                  <tr key={keyOf(item)}>
                    <td>{item.code}</td>
                    <td className="intake-wrap">{item.name}</td>
                    <td>{item.brand_name ?? "—"}</td>
                    <td>{item.category_name ?? "—"}</td>
                    <td>{item.qty}</td>
                    <td>{money(item.listed_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
