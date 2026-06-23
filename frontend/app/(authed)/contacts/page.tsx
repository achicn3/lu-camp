"use client";
// /contacts 會員/賣方（F4 會員中心，docs/17）：搜尋（姓名/電話）＋清單＋建檔。
// national_id 不可明文/部分搜尋（後端僅以 blind index 精確比對）；清單一律遮罩 PII。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { rolesLabel } from "@/features/member/labels";
import { isValidNationalId } from "@/features/member/national-id";

type Contact = components["schemas"]["ContactRead"];
type ContactRole = components["schemas"]["ContactRole"];

const ALL_ROLES: ContactRole[] = ["MEMBER", "SELLER", "CONSIGNOR"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function CreateMemberCard({ onCreated }: { onCreated: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [roles, setRoles] = useState<ContactRole[]>(["MEMBER"]);
  const mutation = useMutation({
    mutationFn: async (input: {
      name: string;
      phone: string | null;
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

export default function ContactsPage() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [submittedQ, setSubmittedQ] = useState("");

  const list = useQuery({
    queryKey: ["contacts", "list", submittedQ],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts", {
        params: { query: { q: submittedQ || undefined, limit: 50 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取會員清單失敗");
      return data;
    },
  });

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmittedQ(q.trim());
  }

  return (
    <section>
      <h1 className="page-title">會員 / 賣方</h1>
      <div className="card-stack">
        <form className="card member-search" onSubmit={onSearch}>
          <label className="field">
            <span className="field-label">搜尋（姓名 / 電話）</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="輸入姓名或電話"
            />
          </label>
          <button type="submit" className="btn-primary">
            搜尋
          </button>
        </form>

        <div className="card">
          <h2>清單</h2>
          {list.isPending ? (
            <p>載入中…</p>
          ) : list.isError ? (
            <p role="alert" className="form-error">
              {list.error.message}
            </p>
          ) : list.data.length === 0 ? (
            <p className="empty-state">查無會員。可調整搜尋或於下方建檔。</p>
          ) : (
            <ul className="member-list">
              {list.data.map((c: Contact) => (
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

        <CreateMemberCard
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: ["contacts", "list"] });
          }}
        />
      </div>
    </section>
  );
}
