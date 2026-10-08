"use client";
// 登錄／修改一張進項發票（docs/70 §5）：選供應商 → 勾它還沒開發票的收貨（可多批、跨採購單）→
// 照發票填號碼、日期與三個金額。勾選的收貨合計與發票含稅不同只提醒、不擋（運費、折扣、尾差）。
// 修改與刪除限管理者（後端也擋）；刪除後那幾批收貨回到「還沒開發票」。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  dt,
  extractDetail,
  type InputInvoice,
  money,
  type ReceiptAmount,
} from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import { formatNtd, parseNtd } from "@/lib/money";

const INVOICE_NUMBER = /^[A-Z]{2}\d{8}$/;

function wholeNtd(raw: string): number | null {
  const parsed = parseNtd(raw.trim());
  return parsed !== null && parsed >= 0 ? parsed : null;
}

export function InputInvoiceForm({
  invoice,
  initialSupplierId = null,
  initialReceiptIds = [],
  onSaved,
  onDeleted,
}: {
  /** 修改既有發票；沒給就是登錄新的。 */
  invoice?: InputInvoice;
  /** 從採購單明細「登錄發票」進來時帶入的供應商與那一批收貨。 */
  initialSupplierId?: number | null;
  initialReceiptIds?: number[];
  onSaved: (invoice: InputInvoice) => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const [supplierId, setSupplierId] = useState<number | null>(
    invoice?.supplier_id ?? initialSupplierId,
  );
  const [checked, setChecked] = useState<Set<number>>(
    () => new Set(invoice ? invoice.receipts.map((r) => r.receipt_id) : initialReceiptIds),
  );
  const [number, setNumber] = useState(invoice?.invoice_number ?? "");
  const [date, setDate] = useState(invoice?.invoice_date ?? "");
  const [net, setNet] = useState(invoice ? String(parseNtd(invoice.invoice_net) ?? "") : "");
  const [tax, setTax] = useState(invoice ? String(parseNtd(invoice.invoice_tax) ?? "") : "");
  const [total, setTotal] = useState(
    invoice ? String(parseNtd(invoice.invoice_total) ?? "") : "",
  );
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const suppliers = useQuery({
    queryKey: ["suppliers", "all-for-invoices"],
    queryFn: async () =>
      (
        await api.GET("/api/v1/suppliers", {
          params: { query: { include_inactive: true, limit: 200, offset: 0 } },
        })
      ).data ?? [],
  });
  const uninvoiced = useQuery({
    queryKey: ["input-invoices", "uninvoiced", supplierId],
    enabled: supplierId !== null,
    queryFn: async () => {
      const { data, error: err } = await api.GET(
        "/api/v1/suppliers/{supplier_id}/uninvoiced-receipts",
        { params: { path: { supplier_id: supplierId ?? 0 } } },
      );
      if (!data) throw new Error(extractDetail(err) ?? "讀取收貨失敗");
      return data;
    },
  });
  // 可勾的收貨＝這家還沒開發票的＋（修改時）這張發票原本就涵蓋的。
  const candidates: ReceiptAmount[] = [
    ...new Map(
      [
        ...(invoice && supplierId === invoice.supplier_id ? invoice.receipts : []),
        ...(uninvoiced.data ?? []),
      ].map((r) => [r.receipt_id, r]),
    ).values(),
  ].sort((a, b) => a.received_at.localeCompare(b.received_at) || a.receipt_id - b.receipt_id);
  const selected = candidates.filter((r) => checked.has(r.receipt_id));
  const selectedTotal = selected.reduce((sum, r) => sum + (parseNtd(r.amount) ?? 0), 0);
  const totalNum = wholeNtd(total);

  const save = useMutation({
    mutationFn: async () => {
      if (supplierId === null) throw new Error("請選擇供應商");
      if (selected.length === 0) throw new Error("請至少勾選一批這張發票涵蓋的收貨");
      const invoiceNumber = number.trim().toUpperCase();
      if (!INVOICE_NUMBER.test(invoiceNumber)) throw new Error("發票號碼是 2 個英文字母＋8 個數字");
      if (date === "") throw new Error("請填發票日期");
      const [n, t, s] = [wholeNtd(net), wholeNtd(tax), wholeNtd(total)];
      if (n === null || t === null || s === null || s <= 0) {
        throw new Error("未稅金額、稅額、含稅金額請照發票填整數元");
      }
      if (n + t !== s) throw new Error("未稅金額＋稅額要等於含稅金額，請對照發票再看一次");
      const body = {
        supplier_id: supplierId,
        receipt_ids: selected.map((r) => r.receipt_id),
        invoice_number: invoiceNumber,
        invoice_date: date,
        invoice_net: String(n),
        invoice_tax: String(t),
        invoice_total: String(s),
      };
      const { data, error: err } = invoice
        ? await api.PUT("/api/v1/purchase-input-invoices/{invoice_id}", {
            params: { path: { invoice_id: invoice.id } },
            body,
          })
        : await api.POST("/api/v1/purchase-input-invoices", { body });
      if (!data) throw new Error(extractDetail(err) ?? "儲存發票失敗");
      return data;
    },
    onSuccess: (saved) => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["input-invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      onSaved(saved);
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (!invoice) return;
      const { error: err, response } = await api.DELETE(
        "/api/v1/purchase-input-invoices/{invoice_id}",
        { params: { path: { invoice_id: invoice.id } } },
      );
      if (!response.ok) throw new Error(extractDetail(err) ?? "刪除發票失敗");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["input-invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      onDeleted?.();
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggle(receiptId: number) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(receiptId)) next.delete(receiptId);
      else next.add(receiptId);
      return next;
    });
  }

  const busy = save.isPending || remove.isPending || save.isSuccess;
  return (
    <form
      className="pur-create-page"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <section className="card pur-create-step">
        <h2>
          <span className="pur-step-no">1</span>供應商
        </h2>
        <label className="field">
          <span className="field-label">開這張發票的供應商</span>
          <select
            aria-label="供應商"
            value={supplierId ?? ""}
            onChange={(e) => {
              setSupplierId(e.target.value === "" ? null : Number(e.target.value));
              setChecked(new Set());
            }}
          >
            <option value="">請選擇</option>
            {(suppliers.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="card pur-create-step">
        <h2>
          <span className="pur-step-no">2</span>這張發票涵蓋的收貨
        </h2>
        {supplierId === null ? (
          <p className="empty-state">先選供應商，就會列出這家還沒開發票的收貨。</p>
        ) : uninvoiced.isPending ? (
          <p>載入中…</p>
        ) : uninvoiced.isError ? (
          <p role="alert" className="form-error">
            {uninvoiced.error.message}
          </p>
        ) : candidates.length === 0 ? (
          <p className="empty-state">這家供應商沒有還沒開發票的收貨。</p>
        ) : (
          <div className="pur-lines-wrap">
            <table className="data-table pur-invoice-receipts">
              <thead>
                <tr>
                  <th />
                  <th>採購單</th>
                  <th>收貨時間</th>
                  <th>這批金額</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((r) => (
                  <tr key={r.receipt_id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`採購單 #${r.purchase_order_id} ${dt(r.received_at)} 收貨`}
                        checked={checked.has(r.receipt_id)}
                        onChange={() => toggle(r.receipt_id)}
                      />
                    </td>
                    <td>#{r.purchase_order_id}</td>
                    <td>{dt(r.received_at)}</td>
                    <td className="money">{money(r.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card pur-create-step">
        <h2>
          <span className="pur-step-no">3</span>照發票填
        </h2>
        <fieldset className="pur-invoice-fields">
          <label className="field">
            <span className="field-label">發票號碼（2 英文＋8 數字）</span>
            <input
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              placeholder="AB12345678"
              maxLength={10}
              aria-label="發票號碼"
            />
          </label>
          <label className="field">
            <span className="field-label">發票日期</span>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              aria-label="發票日期"
            />
          </label>
          <label className="field">
            <span className="field-label">未稅金額（整數元）</span>
            <input
              value={net}
              onChange={(e) => setNet(e.target.value)}
              inputMode="numeric"
              aria-label="發票未稅金額"
            />
          </label>
          <label className="field">
            <span className="field-label">稅額（整數元）</span>
            <input
              value={tax}
              onChange={(e) => setTax(e.target.value)}
              inputMode="numeric"
              aria-label="發票稅額"
            />
          </label>
          <label className="field">
            <span className="field-label">含稅金額（整數元）</span>
            <input
              value={total}
              onChange={(e) => setTotal(e.target.value)}
              inputMode="numeric"
              aria-label="發票含稅金額"
            />
          </label>
        </fieldset>
        {selected.length > 0 && (
          <p className="hint pur-notice" role="status">
            勾選的 {selected.length} 批收貨合計 {formatNtd(selectedTotal)}
            {totalNum !== null && totalNum > 0 && totalNum !== selectedTotal
              ? `，發票含稅 ${formatNtd(totalNum)}，差 ${formatNtd(Math.abs(totalNum - selectedTotal))}（運費、折扣或尾差都可能，照發票填即可）`
              : ""}
          </p>
        )}
      </section>

      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="card pur-create-footer">
        <div className="pur-create-actions">
          {invoice && onDeleted && (
            <button
              type="button"
              className="btn-ghost pur-cancel-btn"
              disabled={busy}
              onClick={() => (confirmingDelete ? remove.mutate() : setConfirmingDelete(true))}
            >
              {remove.isPending ? "刪除中…" : confirmingDelete ? "確定刪除這張發票" : "刪除發票"}
            </button>
          )}
          <button type="submit" className="btn-primary" disabled={busy}>
            {save.isPending ? "儲存中…" : invoice ? "儲存修改" : "登錄發票"}
          </button>
        </div>
      </div>
    </form>
  );
}
