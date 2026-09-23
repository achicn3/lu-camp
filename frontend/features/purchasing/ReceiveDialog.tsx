"use client";
// 收貨入庫對話框：輸入本次各項實收量（可分批）＋選填進項發票。
// 冪等：送出前先把 body 與鍵存起來；回應遺失或重整後以「原 body＋原鍵」重播和解，後端只入庫一次。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { lineRemaining } from "@/features/purchasing/purchasing";
import {
  canDiscardReceivePending,
  extractDetail,
  type PurchaseOrder,
  type PurchaseOrderReceiveBody,
} from "@/features/purchasing/shared";
import { api } from "@/lib/api";
import {
  clearPendingReceive,
  loadPendingReceive,
  type PendingReceive,
  savePendingReceive,
} from "@/lib/idempotency";
import { useBodyScrollLock } from "@/lib/useBodyScrollLock";
import { newIdempotencyKey } from "@/lib/uuid";

export function ReceiveDialog({
  po,
  productName,
  onClose,
  onReceived,
}: {
  po: PurchaseOrder;
  productName: (catalogProductId: number) => string;
  onClose: () => void;
  /** 收貨成功；notice 為和解上一次未確認收貨時要提醒店員的訊息（否則 null）。 */
  onReceived: (notice: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const [receiveError, setReceiveError] = useState<string | null>(null);
  // 本次各明細實收量（line_id → 字串），預設帶入待收量。
  const [receiveQty, setReceiveQty] = useState<Record<number, string>>(() =>
    Object.fromEntries(po.lines.map((l) => [l.id, String(lineRemaining(l.qty, l.received_qty))])),
  );
  // 進項發票（選填；全空＝不登錄，可事後補登）。三個金額照錄原始發票，不在前端重算。
  const [invNumber, setInvNumber] = useState("");
  const [invDate, setInvDate] = useState("");
  const [invNet, setInvNet] = useState("");
  const [invTax, setInvTax] = useState("");
  const [invTotal, setInvTotal] = useState("");
  useBodyScrollLock(true);

  const receive = useMutation({
    mutationFn: async (po: PurchaseOrder): Promise<{ reconciled: boolean }> => {
      const receiveUrl = "/api/v1/purchase-orders/{purchase_order_id}/receive" as const;
      // 1) 先和解上一次未確認的收貨：以「原 body＋原鍵」重播（冪等）。避免重整後 PO 待收量已變、
      //    卻以新待收量沿用舊鍵重送而永久 409 卡死（Codex 第三輪）。
      const pending = loadPendingReceive(po.id);
      if (pending) {
        const { data, response } = await api.POST(receiveUrl, {
          params: {
            path: { purchase_order_id: po.id },
            header: { "Idempotency-Key": pending.key },
          },
          body: pending.body as PurchaseOrderReceiveBody,
        });
        if (data) {
          clearPendingReceive(po.id);
          return { reconciled: true };
        }
        // 重播非成功：僅「確定未提交」的 4xx 可丟棄舊鍵、續本次收貨；否則保留、請店員稍後再試。
        if (!canDiscardReceivePending(response)) {
          throw new Error("上一次收貨狀態未定，請稍後再試。");
        }
        clearPendingReceive(po.id);
      }
      // 2) 本次收貨（新鍵）
      const parsedLines = po.lines.map((line) => {
        const raw = (receiveQty[line.id] ?? "").trim();
        const qty = raw === "" ? 0 : Number(raw);
        if (!Number.isFinite(qty) || !Number.isInteger(qty) || qty < 0) {
          throw new Error("本次實收量必須為正整數");
        }
        return { line, qty };
      });
      const lines = parsedLines
        .filter(({ qty }) => qty > 0)
        .map(({ line, qty }) => ({ line_id: line.id, qty }));
      if (lines.length === 0) throw new Error("請至少輸入一項的本次實收量");
      for (const { line, qty } of parsedLines) {
        if (qty > lineRemaining(line.qty, line.received_qty)) {
          throw new Error("本次實收量不可超過待收量");
        }
      }
      const invoiceParts = [
        invNumber.trim(),
        invDate,
        invNet.trim(),
        invTax.trim(),
        invTotal.trim(),
      ];
      const hasInvoice = invoiceParts.some((value) => value !== "");
      if (hasInvoice && invoiceParts.some((value) => value === "")) {
        throw new Error("進項發票的號碼、日期、未稅金額、稅額與含稅金額都要填寫");
      }
      const body: PurchaseOrderReceiveBody = hasInvoice
        ? {
            lines,
            invoice: {
              invoice_number: invNumber.trim().toUpperCase(),
              invoice_date: invDate,
              invoice_net: invNet.trim(),
              invoice_tax: invTax.trim(),
              invoice_total: invTotal.trim(),
            },
          }
        : { lines };
      const key = newIdempotencyKey();
      // 送出前先連同 body 持久化：回應遺失/重整後由此重播和解，後端只入庫一次（防重複入庫）。
      const entry: PendingReceive = { key, body };
      savePendingReceive(po.id, entry);
      const { data, error, response } = await api.POST(receiveUrl, {
        params: {
          path: { purchase_order_id: po.id },
          header: { "Idempotency-Key": key },
        },
        body,
      });
      if (!data) {
        if (canDiscardReceivePending(response)) clearPendingReceive(po.id);
        throw new Error(extractDetail(error) ?? "收貨失敗，請稍後再試");
      }
      clearPendingReceive(po.id);
      return { reconciled: false };
    },
    onSuccess: (result) => {
      setReceiveError(null);
      void queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["catalog-products"] });
      onReceived(
        result.reconciled
          ? "偵測到上一次收貨尚未確認、已為您同步；請確認待收數量後再收剩餘。"
          : null,
      );
    },
    onError: (err: Error) => setReceiveError(err.message),
  });

  return (
        <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="確認收貨">
          <div className="card pos-dialog pur-receive-dialog">
            <h2>收貨入庫</h2>
            <p className="hint">
              採購單 #{po.id}（{po.supplier_name}）。輸入本次各項實收量，
              未收足將轉為「部分到貨」，可日後再收。
            </p>
            <div className="pur-lines-wrap">
              <table className="data-table pur-receive-table">
                <thead>
                  <tr>
                    <th>商品</th>
                    <th>訂購</th>
                    <th>已收</th>
                    <th>待收</th>
                    <th>本次實收</th>
                  </tr>
                </thead>
                <tbody>
                  {po.lines.map((line) => {
                    const name = productName(line.catalog_product_id);
                    const remaining = lineRemaining(line.qty, line.received_qty);
                    return (
                      <tr key={line.id}>
                        <td>{name}</td>
                        <td>{line.qty}</td>
                        <td>{line.received_qty}</td>
                        <td>{remaining}</td>
                        <td>
                          <input
                            type="number"
                            min={0}
                            max={remaining}
                            step={1}
                            className="pur-qty"
                            aria-label={`本次實收 ${name}`}
                            value={receiveQty[line.id] ?? ""}
                            onChange={(e) =>
                              setReceiveQty((prev) => ({ ...prev, [line.id]: e.target.value }))
                            }
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <fieldset className="pur-invoice-fields">
              <legend>進項發票（選填；供應商發票隨貨時一併登錄，漏登可事後補登）</legend>
              <label className="field">
                <span className="field-label">發票號碼（2 英文＋8 數字）</span>
                <input
                  value={invNumber}
                  onChange={(e) => setInvNumber(e.target.value)}
                  placeholder="AB12345678"
                  maxLength={10}
                  aria-label="發票號碼"
                />
              </label>
              <label className="field">
                <span className="field-label">發票日期</span>
                <input
                  type="date"
                  value={invDate}
                  onChange={(e) => setInvDate(e.target.value)}
                  aria-label="發票日期"
                />
              </label>
              <label className="field">
                <span className="field-label">未稅金額（照發票填，整數元）</span>
                <input
                  value={invNet}
                  onChange={(e) => setInvNet(e.target.value)}
                  inputMode="numeric"
                  aria-label="發票未稅金額"
                />
              </label>
              <label className="field">
                <span className="field-label">稅額（照發票填，整數元）</span>
                <input
                  value={invTax}
                  onChange={(e) => setInvTax(e.target.value)}
                  inputMode="numeric"
                  aria-label="發票稅額"
                />
              </label>
              <label className="field">
                <span className="field-label">含稅金額（照發票填，整數元）</span>
                <input
                  value={invTotal}
                  onChange={(e) => setInvTotal(e.target.value)}
                  inputMode="numeric"
                  aria-label="發票含稅金額"
                />
              </label>
            </fieldset>
            {receiveError !== null && (
              <p role="alert" className="form-error">
                {receiveError}
              </p>
            )}
            <div className="pos-dialog-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={receive.isPending}
                onClick={() => receive.mutate(po)}
              >
                {receive.isPending ? "收貨中…" : "確認收貨"}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={receive.isPending}
                onClick={onClose}
              >
                取消
              </button>
            </div>
          </div>
        </div>
  );
}
