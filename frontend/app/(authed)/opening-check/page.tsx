"use client";
// /opening-check 開店前檢查（裁示 2026-09-17）。
//
// 每天第一次打開系統會自動帶到這頁，全部綠燈才不再跳。四項裁示：未完成**不擋**其他頁面
// （只跳一次＋導覽列紅點）；自動項目沒過可以略過、不必填原因；狀態以「每店每日」為單位
// （任何一台裝置完成就算完成）；自訂項目在設定頁增減。
//
// 兩種項目本質不同，所以分開呈現：
//   - 自動：系統判定（今日已開帳、各裝置連線），店員**不能**用手打勾，否則只是儀式。
//   - 手動：店主自訂的事項，看過按確認。
//
// 裝置狀態直接問 hardware-agent（與列印同一條路，代理就在店內電腦上），不經後端；
// 但「今天略過哪一台」存後端，否則換一台裝置又要重按。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import {
  deviceKey,
  devicePasses,
  useOpeningCheckStatus,
} from "@/features/openingcheck/status";
import { api } from "@/lib/api";

type CheckState = "pass" | "fail" | "skipped";

const DEVICE_LABEL: Record<string, string> = {
  RECEIPT_PRINTER: "收據／發票機",
  LABEL_PRINTER: "標籤機",
  CASH_DRAWER: "錢櫃",
  SCANNER: "掃描器",
};

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function StateDot({ state }: { state: CheckState }) {
  const tone = state === "pass" ? "ok" : state === "skipped" ? "warn" : "danger";
  const text = state === "pass" ? "正常" : state === "skipped" ? "今天略過" : "待處理";
  return <span className={`opening-dot opening-dot-${tone}`}>{text}</span>;
}

function AutoRow({
  label,
  state,
  hint,
  href,
  actionLabel,
  onSkip,
  busy,
}: {
  label: string;
  state: CheckState;
  hint: string;
  href: string;
  actionLabel: string;
  onSkip: () => void;
  busy: boolean;
}) {
  return (
    <li className="opening-item">
      <div className="opening-item-main">
        <StateDot state={state} />
        <div>
          <p className="opening-item-label">{label}</p>
          <p className="hint">{hint}</p>
        </div>
      </div>
      {state !== "pass" && (
        <div className="opening-item-actions">
          <Link className="btn-ghost" href={href}>
            {actionLabel}
          </Link>
          {state !== "skipped" && (
            <button type="button" className="btn-ghost" disabled={busy} onClick={onSkip}>
              今天略過
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export default function OpeningCheckPage() {
  const queryClient = useQueryClient();

  // 狀態與「算不算完成」與導覽列共用同一份判斷，免得頁面說沒做完、選單說做完了。
  const { today, deviceList, devicesUnknown, skipped, allClear } =
    useOpeningCheckStatus(true);

  const skip = useMutation({
    mutationFn: async (key: string) => {
      const { data, error } = await api.POST("/api/v1/opening-check/today/skip", {
        body: { key },
      });
      if (!data) throw new Error(extractDetail(error) ?? "略過失敗");
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["opening-check"] }),
  });

  const setDone = useMutation({
    mutationFn: async ({ id, done }: { id: number; done: boolean }) => {
      const { data, error } = await api.POST("/api/v1/opening-check/today/items/{item_id}", {
        params: { path: { item_id: id } },
        body: { done },
      });
      if (!data) throw new Error(extractDetail(error) ?? "更新失敗");
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["opening-check"] }),
  });

  // 打勾／略過失敗時要講出來：不講的話畫面看起來就是「按了沒反應」。
  const actionError = skip.error?.message ?? setDone.error?.message ?? null;
  const data = today.data;
  const busy = skip.isPending || setDone.isPending;

  const autoRows: {
    key: string;
    label: string;
    state: CheckState;
    hint: string;
    href: string;
    actionLabel: string;
  }[] = [];
  if (data !== undefined) {
    // 昨天忘記關帳要講清楚是哪一種問題：籠統說「還沒開帳」會讓店員直接按開帳，
    // 結果被系統擋下（已有開著的班別），然後不知道該怎麼辦。
    const stale = data.cash_session_state === "STALE";
    autoRows.push({
      key: "cash_session",
      label: "今日已開帳",
      state: data.cash_session_state === "OPEN_TODAY"
        ? "pass"
        : skipped.has("cash_session")
          ? "skipped"
          : "fail",
      hint:
        data.cash_session_state === "OPEN_TODAY"
          ? "已開帳，收現與收購付款可以進行"
          : skipped.has("cash_session")
            ? "今天略過，明天會再檢查一次"
            : stale
              ? "昨天的班別還沒關帳。先把昨天的帳結掉，再開今天的帳——不然今天的現金會被算進昨天"
              : "還沒開帳，收現、收購付款都會被擋下",
      href: "/cash",
      actionLabel: stale ? "去結帳" : "去開帳",
    });
  }
  for (const device of deviceList ?? []) {
    const key = deviceKey(device);
    const label = `${DEVICE_LABEL[device.kind] ?? device.kind}（${device.model}）`;
    // 測試模式（fake）不算通過：這頁的承諾是「全綠＝可以開店」，而測試模式按了不會出紙。
    autoRows.push({
      key,
      label,
      state: devicePasses(device) ? "pass" : skipped.has(key) ? "skipped" : "fail",
      hint: devicePasses(device)
        ? "連線正常"
        : skipped.has(key)
          ? "今天略過，明天會再檢查一次"
          : device.driver === "fake"
            ? "測試模式，按了不會有東西出來"
            : (device.probe_error ?? "連不上，相關列印會失敗"),
      href: "/settings",
      actionLabel: "查看裝置",
    });
  }

  const manualItems = data?.items ?? [];
  const autoDone = autoRows.filter((row) => row.state !== "fail").length;
  const doneCount = autoDone + manualItems.filter((item) => item.done).length;
  const total = autoRows.length + manualItems.length;

  return (
    <section className="opening-page">
      <h1 className="page-title">開店前檢查</h1>
      <p className="hint">
        每天第一次打開系統會自動帶到這頁。全部綠燈後今天就不會再跳出來，之後想看隨時從選單
        進來。沒做完也不會擋住結帳，只是導覽列會留一個紅點提醒。
      </p>

      {today.isError && (
        <p role="alert" className="form-error">
          {today.error.message}
        </p>
      )}
      {actionError !== null && (
        <p role="alert" className="form-error">
          {actionError}
        </p>
      )}

      <div className={`card opening-summary${allClear ? " opening-summary-clear" : ""}`}>
        <div>
          <p className="opening-summary-count">
            {doneCount} / {total} 完成
          </p>
          <p className="hint">
            {allClear
              ? "都確認過了，可以開店。今天不會再跳出這一頁。"
              : "還有項目沒處理，處理完這頁就會自動放行。"}
          </p>
        </div>
        {allClear && <span className="opening-dot opening-dot-ok">今日檢查完成</span>}
      </div>

      <div className="card">
        <h2>系統自動檢查</h2>
        <p className="hint">這幾項由系統判定，不能手動打勾。真的處理不了可以今天略過。</p>
        {devicesUnknown && (
          <p role="status" className="hint">
            連不到店內的列印代理，所以看不到各機器的狀態。請確認代理程式有在執行——這段期間
            不會顯示「全部完成」。
          </p>
        )}
        <ul className="opening-list">
          {autoRows.map((row) => (
            <AutoRow
              key={row.key}
              label={row.label}
              state={row.state}
              hint={row.hint}
              href={row.href}
              actionLabel={row.actionLabel}
              busy={busy}
              onSkip={() => skip.mutate(row.key)}
            />
          ))}
        </ul>
        {today.isPending && <p className="hint">載入中…</p>}
      </div>

      <div className="card">
        <h2>今日確認事項</h2>
        <p className="hint">在「設定 → 開店前檢查項目」可以自己增減這些項目。</p>
        <ul className="opening-list">
          {manualItems.map((item) => (
            <li key={item.id} className="opening-item">
              <label className="opening-item-main opening-item-check">
                <input
                  type="checkbox"
                  checked={item.done}
                  aria-label={item.label}
                  disabled={busy}
                  onChange={(e) => setDone.mutate({ id: item.id, done: e.target.checked })}
                />
                <div>
                  <p className="opening-item-label">{item.label}</p>
                </div>
              </label>
              {item.href !== null && !item.done && (
                <div className="opening-item-actions">
                  <Link className="btn-ghost" href={item.href}>
                    前往處理
                  </Link>
                </div>
              )}
            </li>
          ))}
        </ul>
        {data !== undefined && manualItems.length === 0 && (
          <p className="hint">還沒有自訂項目。</p>
        )}
      </div>
    </section>
  );
}
