"use client";
// POS 結帳的會員歸戶：打字就查，不必按按鈕。
//
// 從 /pos 頁面抽出來成獨立元件，讓查找行為可以被單獨測試——原本內嵌在千行的結帳頁裡，
// 要驗「打字有沒有出結果」就得先把整個 POS 撐起來（購物車、收款、客顯…），
// 於是實際上沒有人在測它。
//
// 行為刻意與收購頁的賣方查找一致（debounce 即查）：結帳當下客人站在櫃檯前，多一個
// 按鈕就多一次停頓；兩頁行為不一致本身也是負擔——店員得記住「這頁要按、那頁不用」。
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type ContactRead = components["schemas"]["ContactRead"];

/** 打字到送出查詢之間的等待。太短＝每個按鍵都打一次 API；太長＝店員覺得卡。 */
const SEARCH_DEBOUNCE_MS = 250;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function Money({ value }: { value: number }) {
  return <span className="money">${formatNtd(value)}</span>;
}

export function MemberPanel({
  member,
  onSelect,
  onClear,
  disabled = false,
}: {
  member: ContactRead | null;
  onSelect: (c: ContactRead) => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  const [q, setQ] = useState("");
  // 送進查詢的字串落後於輸入框，避免每個按鍵都打一次 API。
  const [term, setTerm] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setTerm(q.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q]);

  const search = useQuery({
    queryKey: ["pos-member-search", term],
    // 已選定會員後不再查（結果清單會蓋住已歸戶的顯示）；清空輸入即停查。
    enabled: term.length > 0 && member === null && !disabled,
    queryFn: async (): Promise<ContactRead[]> => {
      const { data, error } = await api.GET("/api/v1/contacts", {
        params: { query: { q: term, role: "MEMBER", limit: 8 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "查詢失敗");
      return data;
    },
  });

  const balance = useQuery({
    queryKey: ["store-credit", member?.id],
    enabled: member !== null,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/contacts/{contact_id}/store-credit", {
        params: { path: { contact_id: member!.id } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取餘額失敗");
      return data;
    },
  });

  if (member !== null) {
    const bal = balance.data ? (parseNtd(balance.data.balance) ?? 0) : null;
    return (
      <div className="pos-member pos-member-selected">
        <div>
          <strong>{member.name}</strong>
          {member.phone && <span className="hint"> · {member.phone}</span>}
          <div className="hint">
            點數 {member.member_points} · 購物金餘額{" "}
            {balance.isError ? (
              <span className="balance-error">讀取失敗</span>
            ) : bal === null ? (
              "讀取中…"
            ) : (
              <Money value={bal} />
            )}
          </div>
        </div>
        <button type="button" className="btn-ghost" onClick={onClear} disabled={disabled}>
          取消歸戶
        </button>
      </div>
    );
  }

  // term 落後於輸入框：剛清空輸入時 term 還是舊值、查詢結果也還在，此時不該顯示
  // 上一次的清單（店員會以為那是新輸入的結果）。
  const showResults = q.trim().length > 0 && term.length > 0;

  return (
    <div className="pos-member">
      <div className="pos-member-search">
        <label className="field">
          <span className="field-label">會員歸戶（選填；以購物金付款必填）</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="姓名或電話"
            inputMode="text"
            disabled={disabled}
            autoComplete="off"
          />
        </label>
      </div>
      {showResults && search.isError && (
        <p role="alert" className="form-error">
          {(search.error as Error).message}
        </p>
      )}
      {showResults && search.isSuccess && search.data.length === 0 && (
        <p className="hint">查無符合的會員。</p>
      )}
      {showResults && search.isSuccess && search.data.length > 0 && (
        <ul className="pos-member-results">
          {search.data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => onSelect(c)}
                disabled={disabled}
              >
                {c.name}
                {c.phone ? ` · ${c.phone}` : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
