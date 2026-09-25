"use client";
// 收購的賣方查找／建立／補登身分證（收購頁與收購佇列報到共用；原本內嵌在收購頁，2026-09-25 抽出、行為不變）。
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { isValidNationalId } from "@/features/member/national-id";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { PHONE_HINT, looksLikePhone, normalizeMobile } from "@/lib/phone";

type Contact = components["schemas"]["ContactRead"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

export function SellerSection({
  seller,
  onSelect,
}: {
  seller: Contact | null;
  onSelect: (c: Contact | null) => void;
}) {
  const [q, setQ] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 寄售人已併入賣方（2026-09-01 裁示）：商品是買斷來的還是寄售的，是**商品的屬性**
  // （庫存頁的來源標示），不是人的屬性。兩者在程式裡的待遇本來就完全一樣。
  // 這裡不再送出角色——賣方標記由後端在收購成立時補上。
  const roleLabel = "賣方";

  const results = useQuery({
    queryKey: ["contacts-search", q],
    queryFn: async () => {
      // 號碼的各種寫法由**後端**在搜尋條件裡正規化（contacts/repository._search_select），
      // 前端原樣送即可——同一條規則只留一個實作，才不會兩邊各自漂移。
      const { data } = await api.GET("/api/v1/contacts", { params: { query: { q } } });
      return data ?? [];
    },
    enabled: q.trim().length > 0 && seller === null,
  });

  const createMut = useMutation({
    mutationFn: async (input: { name: string; phone: string; national_id: string }) => {
      const { data, error: apiErr } = await api.POST("/api/v1/contacts", {
        body: {
          name: input.name,
          phone: input.phone,
          national_id: input.national_id,
          // **不在這裡標賣方**：這一刻只是「選好了要跟誰收購」，收購還沒成立。
          // 店員按取消、或收購中途失敗，這個人就會永遠掛著賣方標記——帳面上他賣過
          // 東西、實際上一次都沒有（Codex 對抗式審查 高）。標記由後端在收購成立時
          // 於**同一個交易內**補上（ensure_seller_role），失敗即隨交易回滾。
          member_points: 0,
        },
      });
      if (!data) throw new Error(detail(apiErr) ?? "建立失敗");
      return data;
    },
    onSuccess: (c) => {
      onSelect(c);
      setShowCreate(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  // 補登：為已選取、但尚無身分證字號的既有會員設定 national_id
  // （收購櫃檯一條龍；後端放寬 CLERK 可補登，仍寫稽核）。
  // **只補證號、不標賣方**，理由同上：補登的當下收購還沒成立。
  const backfillMut = useMutation({
    mutationFn: async (input: { id: number; national_id: string }) => {
      const { data, error: apiErr } = await api.PATCH("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: input.id } },
        body: { national_id: input.national_id },
      });
      if (!data) throw new Error(detail(apiErr) ?? "補登失敗");
      return data;
    },
    onSuccess: (c) => onSelect(c),
    onError: (e: Error) => setError(e.message),
  });

  function onBackfill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (seller === null) return;
    const nid = String(new FormData(event.currentTarget).get("national_id") ?? "").trim();
    if (!isValidNationalId(nid)) {
      setError("身分證字號格式或檢核碼不正確，請確認後重新輸入");
      return;
    }
    backfillMut.mutate({ id: seller.id, national_id: nid });
  }

  if (seller !== null) {
    return (
      <div className="card acq-seller">
        <div className="acq-seller-head">
          <div>
            <strong>{seller.name}</strong>
            {seller.phone && <span className="hint"> · {seller.phone}</span>}
            <span className="hint">
              {" "}
              {seller.has_national_id ? "（已建檔）" : "（尚未建檔身分證）"}
            </span>
          </div>
          <button type="button" className="btn-ghost" onClick={() => onSelect(null)}>
            更換
          </button>
        </div>
        {!seller.has_national_id && (
          <form className="acq-backfill" onSubmit={onBackfill}>
            <div className="acq-backfill-row">
              <input
                name="national_id"
                placeholder="補登身分證字號"
                aria-label="補登身分證字號"
                autoComplete="off"
                maxLength={10}
              />
              <button type="submit" className="btn-primary" disabled={backfillMut.isPending}>
                補登身分證字號
              </button>
            </div>
            {error !== null && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
          </form>
        )}
      </div>
    );
  }

  function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    const phone = String(form.get("phone") ?? "").trim();
    const nid = String(form.get("national_id") ?? "").trim();
    if (!name || !phone || !nid) {
      setError("姓名、電話、身分證字號皆必填");
      return;
    }
    const mobile = normalizeMobile(phone);
    if (mobile === null) {
      setError(`${PHONE_HINT}，請確認後重新輸入`);
      return;
    }
    if (!isValidNationalId(nid)) {
      setError("身分證字號格式或檢核碼不正確，請確認後重新輸入");
      return;
    }
    createMut.mutate({ name, phone: mobile, national_id: nid });
  }

  return (
    <div className="card">
      <h2>{roleLabel}</h2>
      <input
        className="acq-search"
        placeholder="以手機或姓名搜尋"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="賣方搜尋"
      />
      {(results.data ?? []).length > 0 && (
        <ul className="acq-results">
          {(results.data ?? []).map((c) => (
            <li key={c.id}>
              <button type="button" className="combo-option" onClick={() => onSelect(c)}>
                {c.name}
                {c.phone ? ` · ${c.phone}` : ""}
                {c.national_id_masked ? `（${c.national_id_masked}）` : "（無證號）"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn-ghost" onClick={() => setShowCreate((v) => !v)}>
        找不到？建立新{roleLabel}
      </button>
      {showCreate && (
        <form className="acq-create-seller" onSubmit={onCreate}>
          {/* 搜尋字直接帶進來：店員剛打完號碼才發現查無此人，沒理由要他再打一次。
              純數字視為電話、其餘視為姓名（裁示 2026-09-16）。 */}
          <input
            name="name"
            placeholder="姓名"
            aria-label="姓名"
            defaultValue={looksLikePhone(q) ? "" : q.trim()}
          />
          <input
            name="phone"
            placeholder="手機"
            aria-label="手機"
            inputMode="tel"
            defaultValue={looksLikePhone(q) ? (normalizeMobile(q) ?? q.trim()) : ""}
          />
          <input name="national_id" placeholder="身分證字號" aria-label="身分證字號" maxLength={10} />
          <button type="submit" className="btn-primary" disabled={createMut.isPending}>
            建立並選取
          </button>
          {error !== null && <p className="form-error">{error}</p>}
        </form>
      )}
    </div>
  );
}
