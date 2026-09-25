"use client";
// /acquisition/intake/listing 待整理上架清單（docs/42 §7）：已付款、還有件沒上架的批次，
// 放最久的排前面；放超過 14 天標紅提醒。掃收件單條碼直接打開那一批。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { batchIdFromSlip } from "@/features/intake/listing";
import { api } from "@/lib/api";
import { formatTaipeiDateTime } from "@/lib/datetime";

const STALE_DAYS = 14;

export default function IntakeListingIndexPage() {
  const router = useRouter();
  const [scan, setScan] = useState("");
  const [scanError, setScanError] = useState<string | null>(null);
  const rows = useQuery({
    queryKey: ["intake-awaiting-listing"],
    queryFn: async () => (await api.GET("/api/v1/intake-batches/awaiting-listing")).data ?? null,
  });

  function openScanned(event: FormEvent) {
    event.preventDefault();
    const id = batchIdFromSlip(scan);
    if (id === null) {
      setScanError("看不懂這個號碼：請掃收件單下方的條碼（IN 開頭）");
      return;
    }
    router.push(`/acquisition/intake/${id}/listing`);
  }

  return (
    <section className="intake-page">
      <div className="pur-page-head">
        <h1 className="page-title">待整理上架</h1>
        <Link href="/acquisition/intake" className="btn-ghost">
          回排隊收購
        </Link>
      </div>
      <p className="hint">
        付完錢、還沒上架的商品。空檔時補品牌、型號、分類，確認售價，上架並印標籤；可以分幾次上。
      </p>
      <form className="card intake-scan" onSubmit={openScanned}>
        <label className="field">
          <span className="field-label">掃收件單條碼</span>
          <input
            aria-label="掃收件單條碼"
            value={scan}
            placeholder="IN000123"
            onChange={(e) => {
              setScan(e.target.value);
              setScanError(null);
            }}
          />
        </label>
        <button type="submit" className="btn-secondary">
          打開這一批
        </button>
        {scanError !== null && (
          <p role="alert" className="form-error">
            {scanError}
          </p>
        )}
      </form>

      <div className="card">
        {rows.isError || rows.data === null ? (
          <p role="alert" className="form-error">讀取失敗，請重新整理。</p>
        ) : !rows.data ? (
          <p className="hint">讀取中…</p>
        ) : rows.data.length === 0 ? (
          <p className="hint">目前沒有待整理的商品。</p>
        ) : (
          <div className="intake-table-scroll">
            <table className="intake-queue">
              <thead>
                <tr>
                  <th scope="col">號碼</th>
                  <th scope="col">賣方</th>
                  <th scope="col">付款時間</th>
                  <th scope="col">放了幾天</th>
                  <th scope="col">上架進度</th>
                  <th scope="col" aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {rows.data.map((row) => {
                  const total = row.pending_count + row.listed_count;
                  const stale = row.days_waiting >= STALE_DAYS;
                  return (
                    <tr key={row.id}>
                      <td className="intake-ticket">{row.ticket_label}</td>
                      <td className="intake-wrap" data-label="賣方">
                        {row.contact_name}
                      </td>
                      <td data-label="付款時間">
                        {formatTaipeiDateTime(row.paid_at, { omitYear: true })}
                      </td>
                      <td data-label="放了幾天">
                        {stale ? (
                          <span className="intake-over">放 {row.days_waiting} 天，該上架了</span>
                        ) : row.days_waiting === 0 ? (
                          "今天"
                        ) : (
                          `${row.days_waiting} 天`
                        )}
                      </td>
                      <td data-label="上架進度">
                        <div className="intake-progress-line">
                          <div
                            className="intake-progress"
                            role="progressbar"
                            aria-label="已上架件數"
                            aria-valuemin={0}
                            aria-valuemax={total}
                            aria-valuenow={row.listed_count}
                          >
                            <span style={{ width: `${total ? (row.listed_count / total) * 100 : 0}%` }} />
                          </div>
                          <span>
                            已上架 {row.listed_count}・待整理 {row.pending_count} 件
                          </span>
                        </div>
                      </td>
                      <td>
                        <Link href={`/acquisition/intake/${row.id}/listing`} className="btn-primary">
                          整理上架
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
