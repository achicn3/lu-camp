"use client";
// 帶回家商品的新增／編輯表單（獨立頁；docs/63 §13、M1d）。
// 新增時從現有商品搜尋挑一個；價格、庫存、分類一律跟著原商品，不在這裡填。照片在編輯頁上傳。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { apiDetail } from "@/features/menu/experienceOptions";
import { MenuPhotoCell } from "@/features/menu/MenuPhotoCell";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Listing = components["schemas"]["RetailListingRead"];
type Write = components["schemas"]["RetailListingWriteRequest"];
type Role = NonNullable<Write["role"]>;

const ROLES: { value: Role; label: string }[] = [
  { value: "bean", label: "咖啡豆" },
  { value: "drip", label: "濾掛" },
];

export function retailRoleLabel(role: string): string {
  return ROLES.find((option) => option.value === role)?.label ?? role;
}

export function RetailForm({ initial, onDone, onCancel }: {
  initial: Listing | null;
  onDone: (saved: Listing) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [productId, setProductId] = useState<number | null>(initial?.catalog_product_id ?? null);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [role, setRole] = useState<Role | "">(initial?.role ?? "");
  const [active, setActive] = useState(initial?.is_active ?? true);
  const [sortOrder, setSortOrder] = useState(String(initial?.sort_order ?? 0));
  const [error, setError] = useState<string | null>(null);
  const keyword = search.trim();

  const products = useQuery({
    queryKey: ["catalog-products", "retail-pick", keyword],
    enabled: initial === null && keyword.length > 0,
    queryFn: async () => {
      const { data, error: e } = await api.GET("/api/v1/catalog-products", {
        params: { query: { q: keyword, limit: 20 } },
      });
      if (!data) throw new Error(apiDetail(e, "搜尋商品失敗"));
      return data;
    },
  });

  const save = useMutation({
    mutationFn: async (body: Write) => {
      const result = initial === null
        ? await api.POST("/api/v1/online-order/retail", { body })
        : await api.PUT("/api/v1/online-order/retail/{listing_id}", {
          params: { path: { listing_id: initial.id } }, body,
        });
      if (!result.data) throw new Error(apiDetail(result.error, "儲存失敗"));
      return result.data;
    },
    onSuccess: onDone,
    onError: (reason: Error) => setError(reason.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (productId === null) { setError("請先挑一個商品"); return; }
    const order = Number(sortOrder);
    if (!Number.isInteger(order) || order < 0 || order > 9999) { setError("排序請填 0–9999 的整數"); return; }
    save.mutate({
      catalog_product_id: productId,
      description: description.trim() || null,
      role: role || null,
      is_active: active,
      sort_order: order,
    });
  }

  return (
    <form className="exp-form retail-form" aria-label="帶回家商品" onSubmit={submit}>
      <div className="exp-form-main">
        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">商品</legend>
          {initial === null ? (
            <>
              <label className="field">
                <span className="field-label">搜尋商品</span>
                <input value={search} placeholder="名稱或 SKU，例如：耶加雪菲" onChange={(e) => setSearch(e.target.value)} />
              </label>
              {products.isError && <p role="alert" className="form-error">{products.error.message}</p>}
              {keyword && products.data?.length === 0 && <p className="hint">找不到符合的商品。</p>}
              <div className="retail-pick" role="radiogroup" aria-label="挑一個商品">
                {(products.data ?? []).map((product) => (
                  <label key={product.id} className={`retail-pick-option${productId === product.id ? " is-on" : ""}`}>
                    <input
                      type="radio"
                      name="retail-product"
                      checked={productId === product.id}
                      onChange={() => setProductId(product.id)}
                    />
                    <span>
                      <b>{product.name}</b>
                      <small>
                        ${formatNtd(parseNtd(product.unit_price) ?? 0)}・庫存 {product.quantity_on_hand}
                        {product.is_active ? "" : "・已停售"}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            </>
          ) : (
            <p className="retail-fixed">
              <b>{initial.product_name}</b>
              <span className="hint">
                ${formatNtd(parseNtd(initial.unit_price) ?? 0)}・庫存 {initial.quantity_on_hand}
                {initial.category_name ? `・${initial.category_name}` : ""}（價格與庫存到庫存頁改）
              </span>
            </p>
          )}
        </fieldset>

        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">線上呈現</legend>
          <label className="field">
            <span className="field-label">介紹</span>
            <textarea value={description} maxLength={300} rows={3} placeholder="例如：柑橘、茉莉花香，淺焙" onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="exp-grid">
            <label className="field">
              <span className="field-label">加購角色</span>
              <select value={role} onChange={(e) => setRole(e.target.value as Role | "")}>
                <option value="">不參與加購</option>
                {ROLES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">排序（小的在前）</span>
              <input type="number" min={0} max={9999} value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
            </label>
          </div>
          <label className="field-toggle">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            <span>啟用（發佈後客人看得到）</span>
          </label>
        </fieldset>

        {initial !== null && (
          <fieldset className="card exp-block">
            <legend className="exp-block-title">照片</legend>
            <MenuPhotoCell
              target="retail"
              item={{ id: initial.id, name: initial.product_name, photo_sha256: initial.photo_sha256 }}
              onChanged={() => void queryClient.invalidateQueries({ queryKey: ["online-retail"] })}
            />
          </fieldset>
        )}
      </div>

      <aside className="exp-form-side">
        {initial === null && <p className="hint">存好之後可以在編輯頁加照片。</p>}
        {error !== null && <p role="alert" className="form-error">{error}</p>}
        <div className="exp-actions">
          <button type="submit" className="btn-primary" disabled={save.isPending}>{save.isPending ? "儲存中…" : "儲存"}</button>
          <button type="button" className="btn-ghost" disabled={save.isPending} onClick={onCancel}>取消</button>
        </div>
      </aside>
    </form>
  );
}
