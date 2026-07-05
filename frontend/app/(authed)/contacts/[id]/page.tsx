"use client";
// /contacts/[id] 會員 360 詳情（F4 會員中心，docs/17 §5）：總覽 / 消費紀錄 / 寄售 /
// 帶來的商品 / 編輯。皆唯讀彙整端點；編輯走 PATCH（national_id/roles 限 MANAGER）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import {
  INVOICE_STATUS_LABELS,
  KIND_LABELS,
  MEMBER_TABS,
  PAYMENT_METHOD_LABELS,
  ROLE_LABELS,
  SETTLEMENT_STATUS_LABELS,
  SOURCE_TYPE_LABELS,
  labelFor,
  rolesLabel,
  type MemberTab,
} from "@/features/member/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatNtd, parseNtd } from "@/lib/money";

type Overview = components["schemas"]["MemberOverviewRead"];
type ContactRole = components["schemas"]["ContactRole"];

const ALL_ROLES: ContactRole[] = ["MEMBER", "SELLER", "CONSIGNOR"];
const PAGE_SIZE = 20;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function Money({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <span className="money">—</span>;
  const n = parseNtd(value);
  return <span className="money">{n === null ? value : `$${formatNtd(n)}`}</span>;
}

function dt(value: string): string {
  return new Date(value).toLocaleString("zh-TW");
}

// ── 總覽 ──
function OverviewTab({ contactId }: { contactId: number }) {
  const q = useQuery({
    queryKey: ["member", contactId, "overview"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/overview", {
        params: { path: { contact_id: contactId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取總覽失敗");
      return data;
    },
  });
  if (q.isPending) return <p>載入中…</p>;
  if (q.isError)
    return (
      <p role="alert" className="form-error">
        {q.error.message}
      </p>
    );
  const o = q.data;
  return (
    <div className="card-stack">
      <div className="card">
        <h2>帳務摘要</h2>
        <dl className="stat-list">
          <div className="stat">
            <dt>購物金餘額</dt>
            <dd>
              <Money value={o.store_credit_balance} />
            </dd>
          </div>
          <div className="stat">
            <dt>會員點數</dt>
            <dd>{o.member_points} 點</dd>
          </div>
          <div className="stat">
            <dt>寄售待撥（PENDING）</dt>
            <dd>
              <Money value={o.pending_consignment_payout} />
            </dd>
          </div>
          <div className="stat">
            <dt>消費筆數</dt>
            <dd>{o.counts.purchases}</dd>
          </div>
          <div className="stat">
            <dt>寄售品數</dt>
            <dd>{o.counts.consigned_items}</dd>
          </div>
        </dl>
      </div>
      <div className="card">
        <h2>近期消費</h2>
        {o.recent_purchases.length === 0 ? (
          <p className="empty-state">尚無消費紀錄。</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>金額</th>
                <th>付款</th>
                <th>品項</th>
              </tr>
            </thead>
            <tbody>
              {o.recent_purchases.map((p) => (
                <tr key={p.sale_id}>
                  <td>{dt(p.created_at)}</td>
                  <td>
                    <Money value={p.total} />
                  </td>
                  <td>{labelFor(PAYMENT_METHOD_LABELS, p.payment_method)}</td>
                  <td>{p.line_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── 消費紀錄 ──
function PurchasesTab({ contactId }: { contactId: number }) {
  const [offset, setOffset] = useState(0);
  const [openSale, setOpenSale] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["member", contactId, "purchases", offset],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/purchases", {
        params: { path: { contact_id: contactId }, query: { limit: PAGE_SIZE, offset } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取消費紀錄失敗");
      return data;
    },
  });
  return (
    <div className="card">
      <h2>消費紀錄</h2>
      {q.isPending ? (
        <p>載入中…</p>
      ) : q.isError ? (
        <p role="alert" className="form-error">
          {q.error.message}
        </p>
      ) : q.data.length === 0 && offset === 0 ? (
        <p className="empty-state">尚無消費紀錄。</p>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>金額</th>
                <th>付款</th>
                <th>狀態</th>
                <th>品項</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((p) => (
                <tr key={p.sale_id}>
                  <td>{dt(p.created_at)}</td>
                  <td>
                    <Money value={p.total} />
                  </td>
                  <td>{labelFor(PAYMENT_METHOD_LABELS, p.payment_method)}</td>
                  <td>{labelFor(INVOICE_STATUS_LABELS, p.invoice_status)}</td>
                  <td>{p.line_count}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => setOpenSale(openSale === p.sale_id ? null : p.sale_id)}
                    >
                      {openSale === p.sale_id ? "收合" : "明細"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {openSale !== null && (
            <PurchaseDetail contactId={contactId} saleId={openSale} />
          )}
          <div className="pager">
            <button
              type="button"
              className="btn-ghost"
              disabled={offset === 0}
              onClick={() => {
                setOpenSale(null); // 換頁收合明細，避免殘留上一頁單據
                setOffset((o) => Math.max(0, o - PAGE_SIZE));
              }}
            >
              上一頁
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={q.data.length < PAGE_SIZE}
              onClick={() => {
                setOpenSale(null);
                setOffset((o) => o + PAGE_SIZE);
              }}
            >
              下一頁
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function PurchaseDetail({ contactId, saleId }: { contactId: number; saleId: number }) {
  const q = useQuery({
    queryKey: ["member", contactId, "purchase", saleId],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/contacts/{contact_id}/purchases/{sale_id}",
        { params: { path: { contact_id: contactId, sale_id: saleId } } },
      );
      if (!data) throw new Error(extractDetail(error) ?? "讀取明細失敗");
      return data;
    },
  });
  if (q.isPending) return <p>載入明細…</p>;
  if (q.isError)
    return (
      <p role="alert" className="form-error">
        {q.error.message}
      </p>
    );
  const d = q.data;
  return (
    <div className="member-subpanel">
      <h3>單號 #{d.sale_id} 明細</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>品項</th>
            <th>數量</th>
            <th>單價</th>
            <th>小計</th>
          </tr>
        </thead>
        <tbody>
          {d.lines.map((line, i) => (
            <tr key={i}>
              <td>{line.description}</td>
              <td>{line.qty}</td>
              <td>
                <Money value={line.unit_price} />
              </td>
              <td>
                <Money value={line.line_total} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="stat-list">
        <div className="stat">
          <dt>未稅</dt>
          <dd>
            <Money value={d.subtotal} />
          </dd>
        </div>
        <div className="stat">
          <dt>稅</dt>
          <dd>
            <Money value={d.tax} />
          </dd>
        </div>
        <div className="stat">
          <dt>總額</dt>
          <dd>
            <Money value={d.total} />
          </dd>
        </div>
      </dl>
      <p className="hint">
        收款：
        {d.tenders
          .map((t) => `${labelFor(PAYMENT_METHOD_LABELS, t.tender_type)} ${t.amount}`)
          .join("、")}
      </p>
    </div>
  );
}

// ── 寄售 ──
function ConsignmentsTab({ contactId }: { contactId: number }) {
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["member", contactId, "consignments", offset],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/consignments", {
        params: { path: { contact_id: contactId }, query: { limit: PAGE_SIZE, offset } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取寄售失敗");
      return data;
    },
  });
  return (
    <div className="card">
      <h2>寄售品</h2>
      {q.isPending ? (
        <p>載入中…</p>
      ) : q.isError ? (
        <p role="alert" className="form-error">
          {q.error.message}
        </p>
      ) : (
        <>
          <div className="member-banner">
            目前待撥金額（PENDING）：
            <Money value={q.data.pending_payout_total} />
          </div>
          {q.data.items.length === 0 && offset === 0 ? (
            <p className="empty-state">尚無寄售品。</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>類型</th>
                  <th>品名 / 編號</th>
                  <th>狀態</th>
                  <th>抽成%</th>
                  <th>應撥</th>
                  <th>結算</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((it, i) => (
                  <tr key={`${it.kind}-${it.code}-${i}`}>
                    <td>{labelFor(KIND_LABELS, it.kind)}</td>
                    <td>
                      {it.name}
                      <span className="row-sub">{it.code}</span>
                    </td>
                    <td>{it.item_status}</td>
                    <td>{it.commission_pct ?? "—"}</td>
                    <td>
                      <Money value={it.payout_amount} />
                    </td>
                    <td>
                      {it.settlement_status === null || it.settlement_status === undefined
                        ? "—"
                        : labelFor(SETTLEMENT_STATUS_LABELS, it.settlement_status)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="pager">
            <button
              type="button"
              className="btn-ghost"
              disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              上一頁
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={q.data.items.length < PAGE_SIZE}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              下一頁
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ── 帶來的商品（買斷 ∪ 寄售）──
function SourcedTab({ contactId }: { contactId: number }) {
  const [sourceType, setSourceType] = useState<"" | "BUYOUT" | "CONSIGNMENT">("");
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["member", contactId, "sourced", sourceType, offset],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/sourced-items", {
        params: {
          path: { contact_id: contactId },
          query: {
            source_type: sourceType || undefined,
            limit: PAGE_SIZE,
            offset,
          },
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取商品來源失敗");
      return data;
    },
  });
  return (
    <div className="card">
      <h2>帶來的商品</h2>
      <div className="member-filters">
        {(["", "BUYOUT", "CONSIGNMENT"] as const).map((opt) => (
          <button
            key={opt || "ALL"}
            type="button"
            className={`chip ${sourceType === opt ? "chip-active" : ""}`}
            onClick={() => {
              setSourceType(opt);
              setOffset(0);
            }}
          >
            {opt === "" ? "全部" : labelFor(SOURCE_TYPE_LABELS, opt)}
          </button>
        ))}
      </div>
      {q.isPending ? (
        <p>載入中…</p>
      ) : q.isError ? (
        <p role="alert" className="form-error">
          {q.error.message}
        </p>
      ) : q.data.length === 0 && offset === 0 ? (
        <p className="empty-state">查無商品。</p>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>來源</th>
                <th>類型</th>
                <th>品名 / 編號</th>
                <th>狀態</th>
                <th>售價</th>
                <th>入庫</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((it, i) => (
                <tr key={`${it.kind}-${it.code}-${i}`}>
                  <td>{labelFor(SOURCE_TYPE_LABELS, it.source_type)}</td>
                  <td>{labelFor(KIND_LABELS, it.kind)}</td>
                  <td>
                    {it.name}
                    <span className="row-sub">{it.code}</span>
                  </td>
                  <td>{it.status}</td>
                  <td>
                    <Money value={it.listed_price} />
                  </td>
                  <td>{dt(it.intake_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <button
              type="button"
              className="btn-ghost"
              disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              上一頁
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={q.data.length < PAGE_SIZE}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              下一頁
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ── 編輯 ──
function EditTab({ contactId, contact }: { contactId: number; contact: Overview["contact"] }) {
  const queryClient = useQueryClient();
  const isManager = decodeSession()?.role === "MANAGER";
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [roles, setRoles] = useState<ContactRole[]>(contact.roles as ContactRole[]);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [nid, setNid] = useState("");

  const patch = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const { data, error: apiError } = await api.PATCH("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: contactId } },
        body: body as components["schemas"]["ContactUpdate"],
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "更新失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setMsg("已更新");
      void queryClient.invalidateQueries({ queryKey: ["member", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["contacts", "list"] });
    },
    onError: (err: Error) => {
      setMsg(null);
      setError(err.message);
    },
  });

  function onSubmitProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    patch.mutate({
      name: String(form.get("name")).trim(),
      phone: String(form.get("phone")).trim() || null,
      address: String(form.get("address")).trim() || null,
      source_note: String(form.get("source_note")).trim() || null,
    });
  }

  function toggleRole(role: ContactRole) {
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));
  }

  async function reveal() {
    setError(null);
    const { data, error: apiError } = await api.GET(
      "/api/v1/contacts/{contact_id}/national-id",
      { params: { path: { contact_id: contactId } } },
    );
    if (!data) {
      setError(extractDetail(apiError) ?? "查看失敗");
      return;
    }
    setRevealed(data.national_id);
  }

  return (
    <div className="card-stack">
      <form className="card" onSubmit={onSubmitProfile}>
        <h2>基本資料</h2>
        <div className="member-form-grid">
          <label className="field">
            <span className="field-label">姓名 *</span>
            <input name="name" defaultValue={contact.name} required />
          </label>
          <label className="field">
            <span className="field-label">電話</span>
            <input name="phone" defaultValue={contact.phone ?? ""} inputMode="tel" />
          </label>
          <label className="field">
            <span className="field-label">住址（切結書顯示用）</span>
            <input name="address" defaultValue={contact.address ?? ""} maxLength={200} />
          </label>
          <label className="field">
            <span className="field-label">備註</span>
            <input name="source_note" defaultValue={contact.source_note ?? ""} />
          </label>
        </div>
        {msg !== null && <p className="form-success">{msg}</p>}
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={patch.isPending}>
          儲存
        </button>
      </form>

      <div className="card">
        <h2>角色與身分證</h2>
        {!isManager ? (
          <p className="hint">變更角色或身分證字號需管理者權限。</p>
        ) : (
          <>
            <fieldset className="member-roles">
              <legend className="field-label">角色</legend>
              {ALL_ROLES.map((role) => (
                <label key={role} className="member-role-check">
                  <input
                    type="checkbox"
                    checked={roles.includes(role)}
                    onChange={() => toggleRole(role)}
                  />
                  <span>{labelFor(ROLE_LABELS, role)}</span>
                </label>
              ))}
            </fieldset>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => patch.mutate({ roles })}
              disabled={patch.isPending}
            >
              更新角色
            </button>
            <hr className="divider" />
            <p className="hint">
              身分證字號：{contact.has_national_id ? "已建檔（加密）" : "未建檔"}
              （收購/寄售角色必填；設定後可加 SELLER/CONSIGNOR）
            </p>
            <div className="member-id-actions">
              <input
                value={nid}
                onChange={(e) => setNid(e.target.value)}
                placeholder="輸入身分證字號"
                autoComplete="off"
              />
              <button
                type="button"
                className="btn-ghost"
                disabled={patch.isPending || nid.trim() === ""}
                onClick={() => patch.mutate({ national_id: nid.trim() })}
              >
                設定
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={patch.isPending}
                onClick={() => patch.mutate({ national_id: null })}
              >
                清除
              </button>
            </div>
            <div className="member-id-actions">
              <button type="button" className="btn-ghost" onClick={() => void reveal()}>
                查看身分證（寫稽核）
              </button>
              {revealed !== null && <span className="money">{revealed}</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function MemberDetailPage() {
  const params = useParams<{ id: string }>();
  const contactId = Number(params.id);
  const [tab, setTab] = useState<MemberTab["key"]>("overview");

  const header = useQuery({
    queryKey: ["member", contactId, "overview"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/overview", {
        params: { path: { contact_id: contactId } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取會員失敗");
      return data;
    },
  });

  return (
    <section>
      <div className="member-head">
        <Link href="/contacts" className="btn-ghost">
          ← 返回
        </Link>
        <h1 className="page-title">
          {header.data ? header.data.contact.name : "會員"}{" "}
          {header.data && (
            <span className="member-head-roles">{rolesLabel(header.data.contact.roles)}</span>
          )}
        </h1>
        {header.data?.contact.address && (
          <p className="hint">住址：{header.data.contact.address}</p>
        )}
      </div>

      {header.isError ? (
        <p role="alert" className="form-error">
          {header.error.message}
        </p>
      ) : (
        <>
          <nav className="member-tabs" aria-label="會員資料分頁">
            {MEMBER_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`member-tab ${tab === t.key ? "member-tab-active" : ""}`}
                aria-current={tab === t.key ? "page" : undefined}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </nav>

          {tab === "overview" && <OverviewTab contactId={contactId} />}
          {tab === "purchases" && <PurchasesTab contactId={contactId} />}
          {tab === "consignments" && <ConsignmentsTab contactId={contactId} />}
          {tab === "sourced" && <SourcedTab contactId={contactId} />}
          {tab === "edit" &&
            (header.data ? (
              <EditTab contactId={contactId} contact={header.data.contact} />
            ) : (
              <p>載入中…</p>
            ))}
        </>
      )}
    </section>
  );
}
