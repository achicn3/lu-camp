"use client";
// /menu 餐飲菜單管理頁（MANAGER 專用）：品項清單（含停售）＋ 建立 ＋ 改名改價/上下架/封存。
// 純呈現：金額為整數元字串，走 OpenAPI 生成 client（禁手刻型別）。餐飲不扣庫存、不折活動。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { useCurrentRole } from "@/lib/useCurrentRole";

type MenuItemRead = components["schemas"]["MenuItemRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

// -- 建立品項 --
function CreateMenuItemForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [category, setCategory] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const p = parseNtd(price);
      if (!name.trim()) throw new Error("請輸入品名");
      if (p === null || p <= 0) throw new Error("售價須為正整數元");
      const { data, error } = await api.POST("/api/v1/menu-items", {
        body: {
          name: name.trim(),
          unit_price: String(p),
          category: category.trim() || null,
          sort_order: 0,
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立品項失敗");
      return data;
    },
    onSuccess: () => {
      setFormError(null);
      setName("");
      setPrice("");
      setCategory("");
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
    <form className="card menu-form" onSubmit={handleSubmit}>
      <h2>新增品項</h2>
      <div className="menu-form-grid">
        <label className="field">
          <span className="field-label">品名</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：手沖-耶加雪菲"
            required
          />
        </label>
        <label className="field">
          <span className="field-label">售價（整數元）</span>
          <input
            inputMode="numeric"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="180"
            required
          />
        </label>
        <label className="field">
          <span className="field-label">分類（選填）</span>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="例如：咖啡"
          />
        </label>
      </div>
      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={create.isPending}>
        {create.isPending ? "建立中…" : "新增品項"}
      </button>
    </form>
  );
}

// -- 單列操作（改價/上下架/封存）--
function MenuItemRow({
  item,
  onChanged,
}: {
  item: MenuItemRead;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState(item.unit_price);
  const [rowError, setRowError] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: async (body: components["schemas"]["MenuItemUpdateRequest"]) => {
      const { data, error } = await api.PATCH("/api/v1/menu-items/{item_id}", {
        params: { path: { item_id: item.id } },
        body,
      });
      if (!data) throw new Error(extractDetail(error) ?? "更新失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      setEditing(false);
      onChanged();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const archive = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.DELETE("/api/v1/menu-items/{item_id}", {
        params: { path: { item_id: item.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "刪除失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      onChanged();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  function savePrice() {
    const p = parseNtd(price);
    if (p === null || p <= 0) {
      setRowError("售價須為正整數元");
      return;
    }
    patch.mutate({ unit_price: String(p) });
  }

  return (
    <tr>
      <td>{item.name}</td>
      <td>{item.category ?? "—"}</td>
      <td>
        {editing ? (
          <span className="menu-edit-price">
            <input
              className="pos-qty"
              inputMode="numeric"
              value={price}
              aria-label={`${item.name} 售價`}
              onChange={(e) => setPrice(e.target.value)}
            />
            <button type="button" className="btn-ghost" onClick={savePrice}>
              儲存
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setEditing(false);
                setPrice(item.unit_price);
                setRowError(null);
              }}
            >
              取消
            </button>
          </span>
        ) : (
          <span className="menu-price-cell">
            <span className="money">{formatNtd(parseNtd(item.unit_price) ?? 0)}</span>
            <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
              改價
            </button>
          </span>
        )}
      </td>
      <td>
        <span className={`inv-badge inv-tone-${item.is_available ? "ok" : "muted"}`}>
          {item.is_available ? "可售" : "停售"}
        </span>
      </td>
      <td>
        <div className="menu-row-actions">
          <button
            type="button"
            className="btn-ghost"
            disabled={patch.isPending}
            onClick={() => patch.mutate({ is_available: !item.is_available })}
          >
            {item.is_available ? "下架" : "上架"}
          </button>
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
          >
            刪除
          </button>
        </div>
        {rowError !== null && (
          <p role="alert" className="form-error menu-row-error">
            {rowError}
          </p>
        )}
      </td>
    </tr>
  );
}

export default function MenuPage() {
  const queryClient = useQueryClient();
  // DB 現值角色（升權未重登也生效；與導覽同源，Codex 波次三第二輪）
  const { isManager } = useCurrentRole();

  const listQuery = useQuery({
    queryKey: ["menu-items", "manage"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-items");
      if (!data) throw new Error(extractDetail(error) ?? "讀取菜單失敗");
      return data;
    },
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["menu-items"] });
  }

  if (!isManager) {
    return (
      <section>
        <h1 className="page-title">餐飲菜單</h1>
        <p>需管理者權限</p>
      </section>
    );
  }

  return (
    <section>
      <h1 className="page-title">餐飲菜單</h1>
      <CreateMenuItemForm onCreated={refresh} />

      <div className="menu-list-section">
        {listQuery.isError && (
          <p role="alert" className="form-error">
            {listQuery.error.message}
          </p>
        )}
        <div className="inv-table-wrap">
          <table className="inv-table">
            <thead>
              <tr>
                <th>品名</th>
                <th>分類</th>
                <th>售價</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(listQuery.data ?? []).map((item) => (
                <MenuItemRow key={item.id} item={item} onChanged={refresh} />
              ))}
            </tbody>
          </table>
          {listQuery.isSuccess && listQuery.data.length === 0 && (
            <p className="hint">尚無餐飲品項</p>
          )}
        </div>
      </div>
    </section>
  );
}
