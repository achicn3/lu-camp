"use client";

import { useQuery } from "@tanstack/react-query";
import { SERIALIZED_GRADES, GRADE_LABEL } from "@/features/acquisition/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

export type DetailEdits = components["schemas"]["SerializedItemUpdateRequest"];
export type InventoryProduct = components["schemas"]["SerializedItemRead"] |
  components["schemas"]["CatalogProductRead"] | components["schemas"]["BulkLotRead"];

export function ProductDetailFields({ item, kind, edits, onChange, disabled }: {
  item: InventoryProduct; kind: "serialized" | "catalog" | "bulk";
  edits: DetailEdits; onChange: (value: DetailEdits) => void; disabled: boolean;
}) {
  const brand = edits.brand_id !== undefined ? edits.brand_id : item.brand_id;
  const category = edits.category_id !== undefined ? edits.category_id : item.category_id;
  const model = edits.product_model_id !== undefined ? edits.product_model_id :
    "product_model_id" in item ? item.product_model_id : null;
  const options = useQuery({
    queryKey: ["inventory-edit-options", brand],
    queryFn: async () => {
      const [brands, categories, models] = await Promise.all([
        api.GET("/api/v1/brands", { params: { query: { limit: 200 } } }),
        api.GET("/api/v1/categories", { params: { query: { limit: 200 } } }),
        api.GET("/api/v1/product-models", { params: { query: { brand_id: brand ?? undefined, limit: 200 } } }),
      ]);
      if (!brands.data || !categories.data || !models.data) throw new Error("讀取商品選項失敗，請稍後重試");
      return { brands: brands.data, categories: categories.data, models: models.data };
    },
  });
  function select(label: string, value: number | null | undefined, choices: { id: number; name: string }[], change: (id: number | null) => void) {
    return <label className="field"><span className="field-label">{label}</span>
      <select aria-label={label} value={value ?? ""} disabled={disabled || options.isPending || options.isError}
        onChange={(e) => change(e.target.value ? Number(e.target.value) : null)}>
        <option value="">未指定</option>
        {value != null && !choices.some((c) => c.id === value) && <option value={value}>目前選擇 #{value}</option>}
        {choices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select></label>;
  }
  return <>
    {options.isError && <p role="alert" className="form-error">{options.error.message}</p>}
    {select("品牌", brand, options.data?.brands ?? [], (id) => onChange({ ...edits, brand_id: id, ...(kind !== "bulk" ? { product_model_id: null } : {}) }))}
    {kind !== "bulk" && select("型號", model, options.data?.models ?? [], (id) => {
      const selected = options.data?.models.find((m) => m.id === id);
      onChange({ ...edits, product_model_id: id, ...(selected ? { brand_id: selected.brand_id } : {}) });
    })}
    {select("種類", category, options.data?.categories ?? [], (id) => onChange({ ...edits, category_id: id }))}
    {kind === "serialized" && "grade" in item && <label className="field"><span className="field-label">成色</span>
      <select aria-label="成色" value={edits.grade ?? item.grade} disabled={disabled}
        onChange={(e) => onChange({ ...edits, grade: e.target.value as DetailEdits["grade"] })}>
        {SERIALIZED_GRADES.map((g) => <option value={g} key={g}>{GRADE_LABEL[g]}</option>)}
      </select></label>}
    <label className="field"><span className="field-label">商品備註</span>
      <textarea aria-label="商品備註" maxLength={500} disabled={disabled} value={edits.note !== undefined ? edits.note ?? "" : item.note ?? ""}
        onChange={(e) => onChange({ ...edits, note: e.target.value || null })} />
    </label>
  </>;
}
