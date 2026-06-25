"use client";
// /contacts 會員/賣方（F4 會員中心，docs/17）：兩個分頁——
//   ① 查找會員：以姓名/電話模糊搜尋，或以身分證字號「精確」查詢（§5：證號不可明文/部分搜尋，
//      僅以 blind index 精確比對，故只接受完整且檢核碼正確的證號）；不預設列出全部會員。
//   ② 所有會員：分頁列出所有會員＋購物金餘額，可用姓名/電話篩。
// national_id 一律遮罩、不回明文；金額以字串傳輸（§6）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { Pagination } from "@/features/common/Pagination";
import { rolesLabel } from "@/features/member/labels";
import { isValidNationalId } from "@/features/member/national-id";
import { formatNtd, parseNtd } from "@/lib/money";

type Contact = components["schemas"]["ContactRead"];
type ContactRole = components["schemas"]["ContactRole"];
type Member = components["schemas"]["MemberWithCreditRead"];

const ALL_ROLES: ContactRole[] = ["MEMBER", "SELLER", "CONSIGNOR"];
const PAGE_SIZE = 50;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function money(value: string): string {
  const parsed = parseNtd(value);
  return parsed === null ? value : formatNtd(parsed);
}

// ── ① 查找會員（姓名/電話模糊；或身分證字號精確）─────────────────────────
type SearchMode = "name_phone" | "national_id";

function SearchTab() {
  const [mode, setMode] = useState<SearchMode>("name_phone");
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState<{ mode: SearchMode; value: string } | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);

  const results = useQuery({
    queryKey: ["contacts", "search", submitted],
    enabled: submitted !== null,
    queryFn: async (): Promise<Contact[]> => {
      if (submitted === null) return [];
      if (submitted.mode === "national_id") {
        const { data, error } = await api.POST("/api/v1/contacts/lookup", {
          body: { national_id: submitted.value },
        });
        if (error) throw new Error(extractDetail(error) ?? "查詢失敗");
        return data ? [data] : [];
      }
      const { data, error } = await api.GET("/api/v1/contacts", {
        params: { query: { q: submitted.value, limit: PAGE_SIZE } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取會員清單失敗");
      return data;
    },
  });

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInputError(null);
    const value = q.trim();
    if (!value) {
      setInputError(mode === "national_id" ? "請輸入完整身分證字號" : "請輸入姓名或電話");
      return;
    }
    if (mode === "national_id" && !isValidNationalId(value)) {
      setInputError(
        "身分證字號格式或檢核碼不正確。為保護個資，身分證僅支援完整、精確查詢（不支援部分比對）。",
      );
      return;
    }
    setSubmitted({ mode, value });
  }

  return (
    <>
      <form className="card member-search" onSubmit={onSearch}>
        <div className="member-search-modes" role="tablist" aria-label="搜尋方式">
          <button
            type="button"
            className={`chip ${mode === "name_phone" ? "chip-active" : ""}`}
            onClick={() => {
              setMode("name_phone");
              setInputError(null);
            }}
          >
            姓名 / 電話
          </button>
          <button
            type="button"
            className={`chip ${mode === "national_id" ? "chip-active" : ""}`}
            onClick={() => {
              setMode("national_id");
              setInputError(null);
            }}
          >
            身分證字號（精確）
          </button>
        </div>
        <label className="field">
          <span className="field-label">
            {mode === "national_id" ? "輸入完整身分證字號" : "輸入姓名或電話"}
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={mode === "national_id" ? "例：A123456789" : "姓名或電話"}
            inputMode={mode === "national_id" ? "text" : "tel"}
            maxLength={mode === "national_id" ? 10 : undefined}
            autoComplete="off"
            aria-label={mode === "national_id" ? "身分證字號搜尋" : "姓名或電話搜尋"}
          />
        </label>
        {mode === "national_id" && (
          <p className="hint">
            為保護個資，身分證字號<b>只能完整、精確查詢</b>（系統以加密索引比對，不做部分搜尋）。
          </p>
        )}
        {inputError !== null && (
          <p role="alert" className="form-error">
            {inputError}
          </p>
        )}
        <button type="submit" className="btn-primary">
          搜尋
        </button>
      </form>

      {submitted !== null && (
        <div className="card">
          <h2>搜尋結果</h2>
          {results.isPending ? (
            <p>載入中…</p>
          ) : results.isError ? (
            <p role="alert" className="form-error">
              {results.error.message}
            </p>
          ) : results.data.length === 0 ? (
            <p className="empty-state">查無符合的會員/賣方。可調整搜尋或於下方建檔。</p>
          ) : (
            <ul className="member-list">
              {results.data.map((c) => (
                <li key={c.id}>
                  <Link href={`/contacts/${c.id}`} className="member-row">
                    <span className="member-row-name">{c.name}</span>
                    <span className="member-row-phone">{c.phone ?? "—"}</span>
                    <span className="member-row-roles">{rolesLabel(c.roles)}</span>
                    <span className="member-row-points">{c.member_points} 點</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

// ── ② 所有會員（分頁清單 + 購物金；可篩姓名/電話）────────────────────────
function AllMembersTab() {
  const [q, setQ] = useState("");
  const [submittedQ, setSubmittedQ] = useState("");
  const [page, setPage] = useState(0);

  const members = useQuery({
    queryKey: ["contacts", "members", submittedQ, page],
    queryFn: async (): Promise<Member[]> => {
      const { data, error } = await api.GET("/api/v1/contacts/members", {
        params: { query: { q: submittedQ || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取會員清單失敗");
      return data;
    },
  });

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(0);
    setSubmittedQ(q.trim());
  }

  const rows = members.data ?? [];

  return (
    <div className="card">
      <form className="member-allsearch" onSubmit={onSearch}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="以姓名或電話篩選"
          aria-label="會員清單篩選"
          inputMode="tel"
        />
        <button type="submit" className="btn-secondary">
          篩選
        </button>
        {submittedQ && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setQ("");
              setSubmittedQ("");
              setPage(0);
            }}
          >
            清除（{submittedQ}）
          </button>
        )}
      </form>

      {members.isPending ? (
        <p>載入中…</p>
      ) : members.isError ? (
        <p role="alert" className="form-error">
          {members.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">{submittedQ ? "查無符合的會員。" : "目前沒有會員。"}</p>
      ) : (
        <>
          <div className="member-table-wrap">
            <table className="data-table member-table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>電話</th>
                  <th>角色</th>
                  <th className="num">點數</th>
                  <th className="num">購物金</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <Link href={`/contacts/${m.id}`} className="member-table-link">
                        {m.name}
                      </Link>
                    </td>
                    <td>{m.phone ?? "—"}</td>
                    <td>{rolesLabel(m.roles)}</td>
                    <td className="num">{m.member_points}</td>
                    <td className="num money">{money(m.store_credit_balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} count={rows.length} pageSize={PAGE_SIZE} onPage={setPage} />
        </>
      )}
    </div>
  );
}

// ── 建檔 ─────────────────────────────────────────────────────────────────
function CreateMemberCard({ onCreated }: { onCreated: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [roles, setRoles] = useState<ContactRole[]>(["MEMBER"]);
  const mutation = useMutation({
    mutationFn: async (input: {
      name: string;
      phone: string;
      national_id: string | null;
      roles: ContactRole[];
    }) => {
      const { data, error: apiError } = await api.POST("/api/v1/contacts", {
        body: { ...input, member_points: 0 },
      });
      if (!data) throw new Error(extractDetail(apiError) ?? "建檔失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onCreated();
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggleRole(role: ContactRole) {
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const name = String(form.get("name")).trim();
    if (!name) {
      setError("請輸入姓名");
      return;
    }
    const phone = String(form.get("phone")).trim();
    if (!phone) {
      setError("請輸入電話");
      return;
    }
    const nationalId = String(form.get("national_id")).trim() || null;
    // 身分證字號為選填，但一旦填寫須通過檢核（避免手動輸入錯誤）；後端 422 為最終防線。
    if (nationalId !== null && !isValidNationalId(nationalId)) {
      setError("身分證字號格式或檢核碼不正確，請確認後重新輸入");
      return;
    }
    mutation.mutate(
      { name, phone, national_id: nationalId, roles },
      { onSuccess: () => formEl.reset() },
    );
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>新增會員/賣方</h2>
      <div className="member-form-grid">
        <label className="field">
          <span className="field-label">姓名 *</span>
          <input name="name" required />
        </label>
        <label className="field">
          <span className="field-label">電話 *</span>
          <input name="phone" inputMode="tel" required />
        </label>
        <label className="field">
          <span className="field-label">身分證字號（收購/寄售必填）</span>
          <input
            name="national_id"
            autoComplete="off"
            inputMode="text"
            maxLength={10}
            placeholder="例：A123456789"
          />
        </label>
      </div>
      <fieldset className="member-roles">
        <legend className="field-label">角色</legend>
        {ALL_ROLES.map((role) => (
          <label key={role} className="member-role-check">
            <input
              type="checkbox"
              checked={roles.includes(role)}
              onChange={() => toggleRole(role)}
            />
            <span>{rolesLabel([role])}</span>
          </label>
        ))}
      </fieldset>
      <p className="hint">身分證字號靜態加密儲存、不明文顯示；收購/寄售對象必填。</p>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        建檔
      </button>
    </form>
  );
}

type Tab = "search" | "all";

export default function ContactsPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("search");

  return (
    <section>
      <h1 className="page-title">會員 / 賣方</h1>
      <div className="settle-tabs" aria-label="會員功能">
        <button
          type="button"
          className={`chip ${tab === "search" ? "chip-active" : ""}`}
          onClick={() => setTab("search")}
        >
          查找會員
        </button>
        <button
          type="button"
          className={`chip ${tab === "all" ? "chip-active" : ""}`}
          onClick={() => setTab("all")}
        >
          所有會員
        </button>
      </div>

      <div className="card-stack">
        {tab === "search" ? <SearchTab /> : <AllMembersTab />}
        <CreateMemberCard
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: ["contacts"] });
          }}
        />
      </div>
    </section>
  );
}
