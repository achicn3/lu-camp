"use client";
// /acquisition/intake 收購佇列（docs/42）：報到收件 → 排隊 → 估價 → 叫號確認。
// 現場只填足以辨認商品與決定價格的資料；建檔上架留到空檔（後續各期）。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { SellerSection } from "@/features/acquisition/SellerSection";
import { STATUS_LABEL } from "@/features/intake/labels";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Contact = components["schemas"]["ContactRead"];
type Batch = components["schemas"]["IntakeBatchRead"];

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function CheckIn({ onCreated }: { onCreated: (batch: Batch) => Promise<void> }) {
  const [seller, setSeller] = useState<Contact | null>(null);
  const [count, setCount] = useState("1");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      if (seller === null) throw new Error("請先選好賣方");
      const declared = parseNtd(count);
      if (declared === null || declared < 1) throw new Error("件數至少 1 件");
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches", {
        body: { contact_id: seller.id, declared_item_count: declared, note: note.trim() || null },
      });
      if (!data) throw new Error(detail(apiErr) ?? "報到失敗");
      return data;
    },
    onSuccess: async (batch) => {
      setSeller(null);
      setCount("1");
      setNote("");
      setError(null);
      await onCreated(batch);
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div className="intake-checkin">
      <SellerSection seller={seller} onSelect={setSeller} />
      <form className="card intake-checkin-form" onSubmit={submit}>
        <h2>報到收件</h2>
        <p className="hint">和客人一起點清實收件數；號碼牌與收件單會印兩份（一份給客人、一份放在商品上）。</p>
        <label className="field">
          <span className="field-label">實收件數</span>
          <input
            inputMode="numeric"
            aria-label="實收件數"
            value={count}
            onChange={(e) => setCount(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">備註（選填）</span>
          <input aria-label="報到備註" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={create.isPending || seller === null}>
          {create.isPending ? "建立中…" : "報到，發號碼"}
        </button>
      </form>
    </div>
  );
}

function Queue({ includeClosed }: { includeClosed: boolean }) {
  const batches = useQuery({
    queryKey: ["intake-batches", includeClosed],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/intake-batches", {
        params: { query: { include_closed: includeClosed } },
      });
      if (!data) throw new Error(detail(error) ?? "讀取排隊清單失敗");
      return data;
    },
    refetchInterval: 15_000, // 兩台平板同時收件時，彼此的新批次要看得到
  });

  if (batches.isError) return <p role="alert" className="form-error">排隊清單讀取失敗，請重新整理。</p>;
  if (!batches.data) return <p className="hint">讀取排隊清單中…</p>;
  if (batches.data.length === 0) return <p className="hint">目前沒有排隊中的客人。</p>;

  return (
    <div className="intake-table-scroll">
    <table className="intake-queue">
      <thead>
        <tr>
          <th scope="col">號碼</th>
          <th scope="col">賣方</th>
          <th scope="col">實收</th>
          <th scope="col">已估</th>
          <th scope="col">估價收購總額</th>
          <th scope="col">狀態</th>
          <th scope="col">報到時間</th>
          <th scope="col" aria-label="操作" />
        </tr>
      </thead>
      <tbody>
        {batches.data.map((b) => (
          <tr key={b.id}>
            <td className="intake-ticket">{b.ticket_label}</td>
            <td className="intake-wrap" data-label="賣方">{b.contact_name}</td>
            <td data-label="實收">{b.declared_item_count} 件</td>
            <td data-label="已估">
              {b.line_count} 項・{b.item_count} 件
              {b.item_count !== b.declared_item_count && b.line_count > 0 && (
                <span className="row-sub">與實收件數不同</span>
              )}
            </td>
            <td className="money" data-label="估價收購總額">${formatNtd(parseNtd(b.deal_total) ?? 0)}</td>
            <td data-label="狀態">{STATUS_LABEL[b.status]}</td>
            <td data-label="報到時間">{formatTaipeiDateTime(b.created_at)}</td>
            <td>
              <Link href={`/acquisition/intake/${b.id}`} className="btn-secondary">
                {b.status === "AWAITING_CONFIRM" ? "叫號確認" : "估價"}
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

export default function IntakeQueuePage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [includeClosed, setIncludeClosed] = useState(false);

  return (
    <section className="intake-page">
      <div className="pur-page-head">
        <h1 className="page-title">排隊收購</h1>
        <Link href="/acquisition" className="btn-ghost">
          直接收購（原收購頁）
        </Link>
      </div>
      <CheckIn
        onCreated={async (batch) => {
          void queryClient.invalidateQueries({ queryKey: ["intake-batches"] });
          // 收件單由估價頁送印（兩份）：先換頁、不等印表機——代理連不上時不能把店員卡在報到畫面。
          router.push(`/acquisition/intake/${batch.id}?print=new`);
        }}
      />
      <div className="card">
        <div className="intake-queue-head">
          <h2>排隊中</h2>
          <label className="campaign-checkbox">
            <input
              type="checkbox"
              checked={includeClosed}
              onChange={(e) => setIncludeClosed(e.target.checked)}
            />
            也顯示已取消／已付款的
          </label>
        </div>
        <Queue includeClosed={includeClosed} />
      </div>
    </section>
  );
}
