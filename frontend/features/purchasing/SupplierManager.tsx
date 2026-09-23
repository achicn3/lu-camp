"use client";
// 供應商建檔、清單、編輯與停用（採購頁「供應商」分頁）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { Pagination } from "@/features/common/Pagination";
import { supplierNameError } from "@/features/purchasing/purchasing";
import { extractDetail, PAGE_SIZE, type Supplier } from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";

// ── 供應商建檔 ───────────────────────────────────────────────
export function SupplierManager() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [taxId, setTaxId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  // 管理清單含停用者（include_inactive）；建單供應商選單另走頁面頂層查詢（預設只取啟用中）。
  const listTotal = useQuery({
    queryKey: ["suppliers", "list", "count", submittedSearch, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/suppliers/count", {
        params: { query: { q: submittedSearch || undefined, include_inactive: true } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取供應商總筆數失敗");
      return data.count;
    },
  });

  const list = useQuery({
    queryKey: ["suppliers", "list", submittedSearch, page],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/suppliers", {
        params: {
          query: {
            q: submittedSearch || undefined,
            include_inactive: true,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取供應商失敗");
      return data;
    },
  });
  const rows = list.data ?? [];

  const setActive = useMutation({
    mutationFn: async ({ id, active }: { id: number; active: boolean }) => {
      const params = { params: { path: { supplier_id: id } } };
      const { data, error } = active
        ? await api.POST("/api/v1/suppliers/{supplier_id}/activate", params)
        : await api.POST("/api/v1/suppliers/{supplier_id}/deactivate", params);
      if (!data) throw new Error(extractDetail(error) ?? "更新供應商狀態失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/suppliers", {
        body: {
          name: name.trim(),
          contact: contact.trim() === "" ? null : contact.trim(),
          tax_id: taxId.trim() === "" ? null : taxId.trim(),
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "建立供應商失敗");
      return data;
    },
    onSuccess: () => {
      setName("");
      setContact("");
      setTaxId("");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const nameErr = supplierNameError(name);
    if (nameErr !== null) {
      setFormError(nameErr);
      return;
    }
    create.mutate();
  }

  return (
    <div className="pur-suppliers">
      <form className="card pur-supplier-form" onSubmit={onSubmit}>
        <h2>新增供應商</h2>
        <label className="field">
          <span>名稱 *</span>
          <input aria-label="供應商名稱" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>聯絡方式</span>
          <input aria-label="聯絡方式" value={contact} onChange={(e) => setContact(e.target.value)} />
        </label>
        <label className="field">
          <span>統一編號</span>
          <input aria-label="統一編號" value={taxId} onChange={(e) => setTaxId(e.target.value)} />
        </label>
        {formError !== null && (
          <p role="alert" className="form-error">
            {formError}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={create.isPending}>
          {create.isPending ? "新增中…" : "新增供應商"}
        </button>
      </form>

      <div className="card pur-supplier-list">
        <h2>供應商清單</h2>
        <form
          className="member-allsearch"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(0);
            setSubmittedSearch(search.trim());
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="以名稱搜尋供應商"
            aria-label="供應商搜尋"
          />
          <button type="submit" className="btn-secondary">
            搜尋
          </button>
          {submittedSearch && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setSearch("");
                setSubmittedSearch("");
                setPage(0);
              }}
            >
              清除（{submittedSearch}）
            </button>
          )}
        </form>
        {list.isPending ? (
          <p>載入中…</p>
        ) : list.isError ? (
          <p role="alert" className="form-error">
            {list.error.message}
          </p>
        ) : rows.length === 0 ? (
          <p className="empty-state">
            {submittedSearch ? "查無符合的供應商。" : "尚無供應商。"}
          </p>
        ) : (
          <>
            {rowError !== null && (
              <p role="alert" className="form-error">
                {rowError}
              </p>
            )}
            <div className="pur-order-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>名稱</th>
                    <th>聯絡方式</th>
                    <th>統編</th>
                    <th>狀態</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} className={s.is_active ? "" : "pur-supplier-inactive"}>
                      <td>{s.name}</td>
                      <td>{s.contact ?? "—"}</td>
                      <td>{s.tax_id ?? "—"}</td>
                      <td>
                        <span className={`inv-badge inv-tone-${s.is_active ? "ok" : "muted"}`}>
                          {s.is_active ? "啟用中" : "已停用"}
                        </span>
                      </td>
                      <td className="pur-row-actions">
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => {
                            setRowError(null);
                            setEditing(s);
                          }}
                        >
                          編輯
                        </button>
                        {s.is_active ? (
                          <button
                            type="button"
                            className="btn-ghost pur-supplier-state-btn pur-supplier-state-btn--deactivate"
                            disabled={setActive.isPending}
                            onClick={() => setActive.mutate({ id: s.id, active: false })}
                          >
                            停用
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn-ghost pur-supplier-state-btn pur-supplier-state-btn--activate"
                            disabled={setActive.isPending}
                            onClick={() => setActive.mutate({ id: s.id, active: true })}
                          >
                            啟用
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <Pagination
          page={page}
          count={rows.length}
          pageSize={PAGE_SIZE}
          total={listTotal.isError ? undefined : listTotal.data}
          unit="家"
          onPage={setPage}
        />
      </div>

      {editing !== null && (
        <SupplierEditModal
          supplier={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["suppliers"] });
          }}
        />
      )}
    </div>
  );
}

// ── 供應商編輯（名稱/聯絡方式/統編）─────────────────────────────
function SupplierEditModal({
  supplier,
  onClose,
  onSaved,
}: {
  supplier: Supplier;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(supplier.name);
  const [contact, setContact] = useState(supplier.contact ?? "");
  const [taxId, setTaxId] = useState(supplier.tax_id ?? "");
  const [error, setError] = useState<string | null>(null);
  useBodyScrollLock(true);

  const update = useMutation({
    mutationFn: async () => {
      const nameErr = supplierNameError(name);
      if (nameErr !== null) throw new Error(nameErr);
      // 只送「有更動」的欄位（稀疏 PATCH）：未動的欄位不重送舊快照，避免蓋掉他人並發修改
      // （Codex 對抗審 medium）。以正規化值比對原值判斷是否更動。
      const nextContact = contact.trim() === "" ? null : contact.trim();
      const nextTaxId = taxId.trim() === "" ? null : taxId.trim();
      const body: components["schemas"]["SupplierUpdate"] = {};
      if (name.trim() !== supplier.name) body.name = name.trim();
      if (nextContact !== (supplier.contact ?? null)) body.contact = nextContact;
      if (nextTaxId !== (supplier.tax_id ?? null)) body.tax_id = nextTaxId;
      if (Object.keys(body).length === 0) return supplier; // 無更動：不打 API
      const { data, error: err } = await api.PATCH("/api/v1/suppliers/{supplier_id}", {
        params: { path: { supplier_id: supplier.id } },
        body,
      });
      if (!data) throw new Error(extractDetail(err) ?? "更新供應商失敗");
      return data;
    },
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="編輯供應商">
      <div className="card pos-dialog">
        <h2>編輯供應商</h2>
        <label className="field">
          <span>名稱 *</span>
          <input aria-label="編輯供應商名稱" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>聯絡方式</span>
          <input
            aria-label="編輯聯絡方式"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
          />
        </label>
        <label className="field">
          <span>統一編號</span>
          <input
            aria-label="編輯統一編號"
            value={taxId}
            onChange={(e) => setTaxId(e.target.value)}
          />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={update.isPending}
            onClick={() => update.mutate()}
          >
            {update.isPending ? "儲存中…" : "儲存"}
          </button>
          <button type="button" className="btn-ghost" disabled={update.isPending} onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
