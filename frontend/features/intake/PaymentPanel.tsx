"use client";
// 排隊收購的簽署與付款（docs/42 §6、§13）：客人在店員平板上勾選後直接簽切結書、選現金或購物金，
// 交還後按付款；付款就成立收購、商品進「待整理」。寄售不付錢、不進切結。
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { useIntakeReceiptPrint } from "@/features/intake/receipt";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { openCashDrawer } from "@/lib/agent";
import { formatNtd, parseNtd } from "@/lib/money";

type Batch = components["schemas"]["IntakeBatchRead"];
type Payout = components["schemas"]["PayoutMethod"];

const PAYOUT_LABEL: Record<string, string> = { CASH: "現金", STORE_CREDIT: "購物金" };
const SIGN_IN_PROGRESS = new Set(["PENDING", "SIGNING"]);
const SIGN_ENDED = new Set(["VOIDED", "EXPIRED", "FAILED", "CONSUMED"]);

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

/** 簽署進行中（或已簽）時，逐列處置要鎖住：改了就和客人簽的不一樣。 */
export function useSignatureLock(batch: Batch | undefined) {
  const taskId = batch?.signature_task_id ?? null;
  const task = useQuery({
    queryKey: ["signing-task", taskId],
    enabled: taskId !== null && batch?.status === "AWAITING_CONFIRM",
    refetchInterval: (q) => (SIGN_IN_PROGRESS.has(q.state.data?.status ?? "") ? 2000 : false),
    queryFn: async () => {
      if (taskId === null) return null;
      const { data } = await api.GET("/api/v1/signing/tasks/{task_id}", {
        params: { path: { task_id: taskId } },
      });
      return data ?? null;
    },
  });
  const status = taskId === null ? null : (task.data?.status ?? null);
  const locked = status !== null && (SIGN_IN_PROGRESS.has(status) || status === "SIGNED");
  return { task, status, locked };
}

export function PaymentPanel({
  batch,
  signature,
  requireSignature,
  drawerOpen,
  onChanged,
}: {
  batch: Batch;
  signature: ReturnType<typeof useSignatureLock>;
  requireSignature: boolean;
  drawerOpen: boolean;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [payout, setPayout] = useState<Payout>("CASH");
  const payable = parseNtd(batch.accepted_total) ?? 0;
  const undecided = batch.lines.filter((l) => l.disposition === "PENDING").map((l) => l.line_no);
  const { status, task } = signature;
  const signed = status === "SIGNED";
  const noTask = status === null || SIGN_ENDED.has(status);
  const signedPayout = task.data?.chosen_payout ?? null;

  const withdraw = useMutation({
    mutationFn: async () => {
      if (batch.signature_task_id == null) return;
      const { response } = await api.POST("/api/v1/signing/tasks/{task_id}/cancel", {
        params: { path: { task_id: batch.signature_task_id } },
        body: { reason_code: "CONTENT_CHANGED", reason: "撤回排隊收購簽署並修改" },
      });
      if (!response.ok) {
        await task.refetch();
        throw new Error("這份簽名已經不能撤回，請重新整理確認是否已付款");
      }
    },
    onSuccess: () => {
      setError(null);
      void task.refetch();
    },
    onError: (e: Error) => setError(e.message),
  });

  const { print: printReceipt, note: receiptNote } = useIntakeReceiptPrint(batch.id);

  const pay = useMutation({
    mutationFn: async () => {
      const method: Payout = signed && signedPayout ? signedPayout : payout;
      const { data, error: apiErr } = await api.POST("/api/v1/intake-batches/{batch_id}/pay", {
        params: { path: { batch_id: batch.id } },
        body: { payout_method: method },
      });
      if (!data) throw new Error(detail(apiErr) ?? "付款失敗");
      return { data, method };
    },
    onSuccess: ({ method }) => {
      setError(null);
      // 付現才開錢櫃；開不了只提示，收購已經成立。
      if (method === "CASH" && payable > 0) {
        setNotice(`請從錢櫃拿 $${formatNtd(payable)} 給客人。`);
        openCashDrawer().catch((err: Error) =>
          setNotice(`請從錢櫃拿 $${formatNtd(payable)} 給客人（錢櫃沒有自動打開：${err.message}）。`),
        );
      } else if (method === "STORE_CREDIT" && payable > 0) {
        setNotice("購物金已存入客人的會員帳戶。");
      }
      onChanged();
    },
    onError: (e: Error) => setError(e.message),
  });

  if (batch.status === "PAID" || batch.status === "PARTIALLY_LISTED" || batch.status === "LISTED") {
    return (
      <div className="card intake-pay" aria-label="付款結果">
        <h2>已付款</h2>
        <p>
          付給客人 <strong className="money">${formatNtd(payable)}</strong>，
          {batch.status === "LISTED"
            ? "收下的商品都已經上架了。"
            : batch.status === "PARTIALLY_LISTED"
              ? "部分商品已經上架，剩下的還在「待整理」，等空檔補資料、貼標籤再上架。"
              : "收下的商品已經放進「待整理」，等空檔補資料、貼標籤再上架。"}
        </p>
        {batch.status !== "LISTED" && (
          <Link href={`/acquisition/intake/${batch.id}/listing`} className="btn-secondary">
            {batch.status === "PARTIALLY_LISTED" ? "繼續整理上架" : "開始整理上架"}
          </Link>
        )}
        {batch.acquisition_ids.length > 0 && (
          <p className="hint">
            已成立收購單 {batch.acquisition_ids.map((id) => `#${id}`).join("、")}；要作廢請到{" "}
            <Link href="/acquisition/records">收購紀錄</Link>。
          </p>
        )}
        {notice !== null && (
          <p className="form-success" role="status">
            {notice}
          </p>
        )}
        {batch.signature_task_id !== null ? (
          <div className="intake-sign">
            <button
              type="button"
              className="btn-primary"
              disabled={printReceipt.isPending}
              onClick={() => printReceipt.mutate()}
            >
              {printReceipt.isPending ? "列印中…" : "列印收購明細（含簽名）"}
            </button>
            {receiptNote !== null && (
              <span role="status" className={printReceipt.isError ? "form-error" : "hint"}>
                {receiptNote}
              </span>
            )}
          </div>
        ) : (
          <p className="hint">這一批付款時沒有請客人簽名，所以沒有收購明細（含簽名）可以印。</p>
        )}
      </div>
    );
  }

  const nothingToPay = payable <= 0;
  const needsSignature = !nothingToPay && (requireSignature || signed);
  const blocked = undecided.length > 0 || batch.accepted_item_count === 0;
  const canPay =
    !blocked &&
    (nothingToPay || signed || !requireSignature) &&
    !SIGN_IN_PROGRESS.has(status ?? "");
  const cashNeeded = !nothingToPay && (signed ? signedPayout === "CASH" : payout === "CASH");

  return (
    <div className="card intake-pay" aria-label="簽名與付款">
      <h2>{nothingToPay ? "確認收下" : "簽名與付款"}</h2>
      {undecided.length > 0 && (
        <p className="hint">第 {undecided.join("、")} 列還沒選處置，選好並儲存後才能請客人簽名。</p>
      )}
      {!blocked && batch.accepted_item_count > 0 && nothingToPay && (
        <p className="hint">這一批只有寄售，現在不付錢、不用簽名；賣出後才分帳。</p>
      )}

      {!nothingToPay && !blocked && (
        <div className="intake-sign">
          {noTask ? (
            <>
              {status !== null && status !== "CONSUMED" && (
                <p role="alert" className="form-error">
                  {status === "EXPIRED"
                    ? "客人太久沒有簽名，請重新送出。"
                    : status === "FAILED"
                      ? "這份簽名失敗了，請重新送出。"
                      : "簽名已撤回。改好後請再交給客人勾選並簽名。"}
                </p>
              )}
              <span className="hint">
                按上面的「交給客人勾選」，客人勾完會在同一台平板上簽切結書、選拿現金或購物金。
              </span>
            </>
          ) : signed ? (
            <>
              <p className="form-success" role="status">
                ✓ 客人已簽名，選擇拿{PAYOUT_LABEL[signedPayout ?? ""] ?? "—"}。
              </p>
              <button
                type="button"
                className="btn-ghost"
                disabled={withdraw.isPending}
                onClick={() => withdraw.mutate()}
              >
                撤回簽名並修改
              </button>
            </>
          ) : (
            <>
              <p role="status">
                客人還沒簽完（平板上的簽署頁）。客人不簽了可以按「撤回簽名並修改」。
              </p>
              <button
                type="button"
                className="btn-ghost"
                disabled={withdraw.isPending}
                onClick={() => withdraw.mutate()}
              >
                撤回簽名並修改
              </button>
            </>
          )}
        </div>
      )}

      {!nothingToPay && !needsSignature && !blocked && noTask && (
        <fieldset className="intake-payout-choice">
          <legend>不簽名直接付款（本店沒有規定一定要簽）</legend>
          {(["CASH", "STORE_CREDIT"] as Payout[]).map((method) => (
            <label key={method} className="campaign-checkbox">
              <input
                type="radio"
                name="intake-payout"
                checked={payout === method}
                onChange={() => setPayout(method)}
              />
              {PAYOUT_LABEL[method]}
            </label>
          ))}
        </fieldset>
      )}

      {cashNeeded && !drawerOpen && canPay && (
        <p className="form-error">還沒開帳：付現金要先到「現金對帳」開帳。</p>
      )}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {canPay && (noTask || signed || nothingToPay) && (
        <button
          type="button"
          className="btn-primary intake-pay-button"
          disabled={pay.isPending || (cashNeeded && !drawerOpen)}
          onClick={() => pay.mutate()}
        >
          {nothingToPay
            ? `確認收下寄售 ${batch.accepted_item_count} 件`
            : `付款 $${formatNtd(payable)}（${PAYOUT_LABEL[signed ? (signedPayout ?? "") : payout] ?? ""}）`}
        </button>
      )}
    </div>
  );
}
