"use client";
// 排隊收購的簽署與付款（docs/42 §6）：整批要付錢的商品送顧客螢幕給客人簽一次，簽完按付款；
// 付款就成立收購、商品進「待整理」。寄售不付錢、不進切結。
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { terminalInstallationId } from "@/features/customer-display/PosCustomerDisplay";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { openCashDrawer, printAcquisitionReceipt } from "@/lib/agent";
import { formatNtd, parseNtd } from "@/lib/money";
import { fetchSignaturePngBase64 } from "@/lib/signature";

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

  const requestSign = useMutation({
    mutationFn: async () => {
      const terminal = (
        await api.POST("/api/v1/customer-display/terminals", {
          body: { installation_id: terminalInstallationId(), name: "主要櫃檯" },
        })
      ).data;
      if (!terminal?.paired_kiosk) throw new Error("請先將這台櫃檯電腦與顧客螢幕配對");
      if (!terminal.paired_kiosk.online) throw new Error("顧客螢幕目前離線，沒辦法請客人簽名");
      const { data, error: apiErr } = await api.POST(
        "/api/v1/intake-batches/{batch_id}/signature",
        { params: { path: { batch_id: batch.id } }, body: { terminal_id: terminal.id } },
      );
      if (!data) throw new Error(detail(apiErr) ?? "送出簽名失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: Error) => setError(e.message),
  });

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

  // 收購明細（含簽名）：整批一張，內容就是客人在顧客螢幕上簽的那份（後端組好）。
  const [receiptNote, setReceiptNote] = useState<string | null>(null);
  const printReceipt = useMutation({
    mutationFn: async () => {
      const { data, error: apiErr } = await api.GET("/api/v1/intake-batches/{batch_id}/receipt", {
        params: { path: { batch_id: batch.id } },
      });
      if (!data) throw new Error(detail(apiErr) ?? "讀不到收購明細");
      await printAcquisitionReceipt({
        storeId: data.store_id,
        acquisitionId: data.acquisition_id,
        reference: data.reference,
        sellerName: data.seller_name,
        items: data.items,
        total: data.total,
        payoutMethod: data.payout_method,
        createdAt: data.signed_at,
        signaturePngBase64: await fetchSignaturePngBase64(data.signature_task_id),
        storeCreditGranted: data.store_credit_granted ?? undefined,
        storeCreditBalanceAfter: data.store_credit_balance_after ?? undefined,
      });
    },
    onSuccess: () => setReceiptNote("收購明細已送出列印，請交給客人。"),
    onError: (e: Error) => setReceiptNote(`收購明細沒有印出來：${e.message}`),
  });

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
          付給客人 <strong className="money">${formatNtd(payable)}</strong>
          ，收下的商品已經放進「待整理」，等空檔補資料、貼標籤再上架。
        </p>
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
                      : "簽名已撤回。改好後請重新送出給客人簽。"}
                </p>
              )}
              <button
                type="button"
                className="btn-primary"
                disabled={requestSign.isPending}
                onClick={() => requestSign.mutate()}
              >
                送到顧客螢幕給客人簽名
              </button>
              <span className="hint">客人會看到要賣的商品和金額，選拿現金或購物金，再簽名。</span>
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
                {status === "SIGNING" ? "客人正在核對內容並簽名…" : "已送到顧客螢幕，等客人打開簽名畫面…"}
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
