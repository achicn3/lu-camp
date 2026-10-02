"use client";
// 同一台店員平板上簽切結書（docs/42 §13；店主 2026-10-02）：客人勾完要賣哪幾件，直接在這裡讀切結書、
// 選拿現金或購物金、簽名。可以隨時「回上一頁」重勾——再進來會建新的簽署任務，內容一定是最新的勾選。
import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { SignatureCanvas, type SignatureCanvasHandle } from "@/app/kiosk/SignatureCanvas";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type Task = components["schemas"]["KioskTaskRead"];
type Payout = "CASH" | "STORE_CREDIT";

type Payload = { image: string; payout: Payout | null };

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function amount(value: unknown): string {
  const n = parseNtd(typeof value === "string" || typeof value === "number" ? String(value) : "");
  return n === null ? "—" : `$${formatNtd(n)}`;
}

function items(content: Task["content"]): { name: string; amount: unknown }[] {
  const raw = content.items;
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => {
    const rec = (item ?? {}) as Record<string, unknown>;
    return { name: typeof rec.name === "string" ? rec.name : "", amount: rec.amount };
  });
}

/** 寄售品（docs/42 §13）：現在不付錢、不進合計，但客人簽的要看得到寄售售價與抽成。 */
function consignments(content: Task["content"]): { name: string; price: unknown; pct: unknown }[] {
  const raw = content.consignments;
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => {
    const rec = (item ?? {}) as Record<string, unknown>;
    return { name: typeof rec.name === "string" ? rec.name : "", price: rec.listed_price, pct: rec.commission_pct };
  });
}

function premium(content: Task["content"]): { amount: unknown; extra: unknown } | null {
  const p = content.store_credit_premium;
  if (p === null || typeof p !== "object") return null;
  const rec = p as Record<string, unknown>;
  return rec.amount === undefined ? null : { amount: rec.amount, extra: rec.extra };
}

export function TabletSigning({
  task,
  onBack,
  onSigned,
}: {
  task: Task;
  onBack: () => void;
  onSigned: (payout: Payout | null) => void;
}) {
  const canvas = useRef<SignatureCanvasHandle>(null);
  // 一個任務一把冪等鍵：回應遺失時用同一把鍵、同一份內容重送，後端回放同一結果。
  const key = useRef(newIdempotencyKey());
  // 送出後沒收到回應（不知道成功沒）：鎖住內容，重送必須一模一樣。
  const [pending, setPending] = useState<Payload | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [payout, setPayout] = useState<Payout | null>(null);
  const [hasInk, setHasInk] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bonus = premium(task.content);
  const consigned = consignments(task.content);
  // 只賣寄售：現在不付錢，不用選現金或購物金。
  const needsPayout = (parseNtd(String(task.content.total ?? "0")) ?? 0) > 0;
  const locked = pending !== null;

  const sign = useMutation({
    mutationFn: async (payload: Payload) => {
      let result;
      try {
        result = await api.POST("/api/v1/signing/tasks/{task_id}/tablet-sign", {
          params: { path: { task_id: task.id } },
          body: {
            signature_image_base64: payload.image,
            ...(payload.payout ? { chosen_payout: payload.payout } : {}),
            idempotency_key: key.current,
          },
        });
      } catch {
        setPending(payload);
        throw new Error("網路不穩，不確定有沒有送到。請再按一次「確認並送出」。");
      }
      if (!result.data) {
        // 伺服器明確拒絕：這把鍵作廢，改好後用新鍵重送。
        key.current = newIdempotencyKey();
        setPending(null);
        const why = detail(result.error) ?? "簽署沒有成功";
        throw new Error(`${why}。可以按「回上一頁」重新確認後再簽，或交給店員處理。`);
      }
      return payload.payout;
    },
    onSuccess: (chosen) => {
      setError(null);
      onSigned(chosen);
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit() {
    if (pending) {
      sign.mutate(pending);
      return;
    }
    const image = canvas.current?.toBase64() ?? null;
    if (!image || (needsPayout && !payout)) return;
    sign.mutate({ image, payout: needsPayout ? payout : null });
  }

  const canSubmit = !sign.isPending && (locked || (agreed && (!needsPayout || payout !== null) && hasInk));
  const seller = typeof task.content.seller_name === "string" ? task.content.seller_name : null;
  const nationalId =
    typeof task.content.national_id_masked === "string" ? task.content.national_id_masked : null;

  return (
    <div className="intake-customer" role="dialog" aria-modal="true" aria-labelledby="intake-sign-title">
      <div className="intake-customer-inner">
        <div className="intake-sign-head">
          <h2 id="intake-sign-title">簽署切結書</h2>
          <button
            type="button"
            className="btn-ghost intake-customer-back"
            disabled={sign.isPending}
            onClick={onBack}
          >
            ← 回上一頁
          </button>
        </div>
        {(seller || nationalId) && (
          <p className="intake-customer-hint">
            賣方 {seller ?? ""}
            {nationalId ? `・身分證 ${nationalId}` : ""}
          </p>
        )}
        <table className="kiosk-items intake-sign-items">
          <thead>
            <tr>
              <th scope="col">要賣的商品</th>
              <th scope="col" className="kiosk-items-amount">
                收購價
              </th>
            </tr>
          </thead>
          <tbody>
            {items(task.content).map((item, i) => (
              <tr key={i}>
                <td>{item.name}</td>
                <td className="kiosk-items-amount">{amount(item.amount)}</td>
              </tr>
            ))}
            {consigned.map((item, i) => (
              <tr key={`c${i}`} className="intake-sign-consign">
                <td>{item.name}</td>
                <td className="kiosk-items-amount">
                  寄售・售價 {amount(item.price)}・抽成 {String(item.pct ?? "—")}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="intake-customer-total">
          合計 <strong>{amount(task.content.total)}</strong>
        </p>
        {consigned.length > 0 && (
          <p className="intake-customer-hint">寄售品現在不付錢，賣出後才分帳（照寄售售價扣抽成）。</p>
        )}

        <div className="kiosk-agreement">
          <h3 className="kiosk-agreement-title">{task.agreement_title ?? "切結書"}</h3>
          <div className="kiosk-agreement-body">{task.agreement_body}</div>
          <label className="kiosk-agree-check">
            <input
              type="checkbox"
              checked={agreed}
              disabled={locked}
              onChange={(e) => setAgreed(e.target.checked)}
            />
            <span>本人已閱讀並同意上述切結書及條款內容</span>
          </label>
        </div>

        {needsPayout && (
        <div className="kiosk-payout">
          <h3 className="kiosk-section-title">
            請選擇收款方式
            <span className="kiosk-required-badge">必選</span>
          </h3>
          <div className="kiosk-payout-options">
            <button
              type="button"
              aria-pressed={payout === "CASH"}
              className={payout === "CASH" ? "kiosk-payout-btn kiosk-payout-btn--active" : "kiosk-payout-btn"}
              disabled={locked}
              onClick={() => setPayout("CASH")}
            >
              <span className="kiosk-payout-label">現金</span>{" "}
              <span className="kiosk-payout-amount">{amount(task.content.total)}</span>
            </button>
            <button
              type="button"
              aria-pressed={payout === "STORE_CREDIT"}
              className={
                payout === "STORE_CREDIT" ? "kiosk-payout-btn kiosk-payout-btn--active" : "kiosk-payout-btn"
              }
              disabled={locked}
              onClick={() => setPayout("STORE_CREDIT")}
            >
              <span className="kiosk-payout-label">購物金</span>{" "}
              <span className="kiosk-payout-amount">
                {amount(bonus ? bonus.amount : task.content.total)}
                {bonus && <span className="kiosk-payout-bonus"> 多得 {amount(bonus.extra)}</span>}
              </span>
            </button>
          </div>
        </div>
        )}

        <div className="kiosk-signature">
          <h3 className="kiosk-section-title">簽名確認</h3>
          <SignatureCanvas ref={canvas} onInkChange={setHasInk} locked={locked} />
        </div>

        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="intake-customer-actions intake-customer-actions-end">
          <button type="button" className="btn-primary intake-customer-btn" disabled={!canSubmit} onClick={submit}>
            {sign.isPending ? "送出中…" : "確認並送出"}
          </button>
        </div>
      </div>
    </div>
  );
}
