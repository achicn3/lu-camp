"use client";
// /settings 管理者設定頁（docs/10 §5 /settings + docs/16 §6）：
// 一般設定（PATCH 僅送變更欄位）、溢價率區（金錢級，二次確認）、溢價率變更歷史。
import "./settings.css";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { clampRate, formatPct, parsePctInput, parseRateInput } from "@/features/settings/helpers";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type SettingsRead = components["schemas"]["SettingsRead"];
type PremiumSuggestionResponse = components["schemas"]["PremiumSuggestionResponse"];
type PremiumRateHistoryRead = components["schemas"]["PremiumRateHistoryRead"];

/** 後端回 401/403 時用以標記「無權限」，與一般讀取失敗區分（驅動「需管理者權限」提示）。 */
class ForbiddenError extends Error {}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function GeneralSettingsCard({
  settings,
  onSaved,
}: {
  settings: SettingsRead;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: async (body: components["schemas"]["SettingsUpdateRequest"]) => {
      const { data, error: apiError } = await api.PATCH("/api/v1/settings", { body });
      if (!data) throw new Error(extractDetail(apiError) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setSuccess(true);
      setError(null);
      onSaved();
    },
    onError: (err: Error) => {
      setError(err.message);
      setSuccess(false);
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    const form = new FormData(event.currentTarget);

    const einvoiceEnabled = form.get("einvoice_enabled") === "on";
    const allowClerkManageCategories = form.get("allow_clerk_manage_categories") === "on";
    const taxRateRaw = String(form.get("tax_rate") ?? "");
    const commissionRaw = String(form.get("default_commission_pct") ?? "");
    const marginRaw = String(form.get("default_margin_pct") ?? "");
    const outflowRaw = String(form.get("monthly_fixed_cash_outflow") ?? "");

    const taxRate = parseRateInput(taxRateRaw);
    if (taxRate === null) {
      setError("稅率請輸入有效百分比數字");
      return;
    }
    // 嚴格整數解析（"50.5"/"50abc" → null，不可前綴解析成 50 存錯值）。
    // 寄售抽成允許 0-100（後端契約 le=100）；定價毛利 0-99（避免除以零）。
    const commission = parsePctInput(commissionRaw, 100);
    if (commission === null) {
      setError("寄售抽成請輸入 0-100 的整數");
      return;
    }
    const margin = parsePctInput(marginRaw);
    if (margin === null) {
      setError("定價目標毛利請輸入 0-99 的整數");
      return;
    }
    const outflow = parseNtd(outflowRaw);
    if (outflow === null || outflow < 0) {
      setError("月固定現金支出請輸入非負整數");
      return;
    }

    // Only send changed fields
    const body: components["schemas"]["SettingsUpdateRequest"] = {};
    if (einvoiceEnabled !== settings.einvoice_enabled) body.einvoice_enabled = einvoiceEnabled;
    if (allowClerkManageCategories !== settings.allow_clerk_manage_categories)
      body.allow_clerk_manage_categories = allowClerkManageCategories;
    // 以數值比較（非字串）：後端預設可能回 "0.05"，前端顯示 5% 會重組成 "0.0500"，
    // 字串不等但數值相同 → 否則會誤判為變更、送出空操作 PATCH 並產生假稽核紀錄。
    if (parseFloat(taxRate) !== parseFloat(settings.tax_rate)) body.tax_rate = taxRate;
    if (commission !== settings.default_commission_pct) body.default_commission_pct = commission;
    if (margin !== settings.default_margin_pct) body.default_margin_pct = margin;
    if (outflow !== parseNtd(settings.monthly_fixed_cash_outflow))
      body.monthly_fixed_cash_outflow = outflow;

    if (Object.keys(body).length === 0) {
      setSuccess(true);
      return;
    }
    mutation.mutate(body);
  }

  const taxPct = (parseFloat(settings.tax_rate) * 100).toString();
  const outflowNum = parseNtd(settings.monthly_fixed_cash_outflow);

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>一般設定</h2>
      <label className="field field-toggle">
        <input
          type="checkbox"
          name="einvoice_enabled"
          defaultChecked={settings.einvoice_enabled}
        />
        <span className="field-label">電子發票開關</span>
      </label>
      <label className="field">
        <span className="field-label">稅率 (%)</span>
        <input name="tax_rate" inputMode="decimal" defaultValue={taxPct} required />
      </label>
      <label className="field">
        <span className="field-label">寄售抽成預設 (%)</span>
        <input
          name="default_commission_pct"
          inputMode="numeric"
          defaultValue={String(settings.default_commission_pct)}
          required
        />
      </label>
      <label className="field">
        <span className="field-label">定價目標毛利 (%)</span>
        <input
          name="default_margin_pct"
          inputMode="numeric"
          defaultValue={String(settings.default_margin_pct)}
          required
        />
      </label>
      <label className="field">
        <span className="field-label">月固定現金支出</span>
        <input
          name="monthly_fixed_cash_outflow"
          inputMode="numeric"
          defaultValue={outflowNum !== null ? formatNtd(outflowNum) : "0"}
          required
        />
      </label>
      <label className="field field-toggle">
        <input
          type="checkbox"
          name="allow_clerk_manage_categories"
          defaultChecked={settings.allow_clerk_manage_categories}
        />
        <span className="field-label">允許店員管理分類</span>
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {success && <p className="form-success">設定已儲存</p>}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        儲存一般設定
      </button>
    </form>
  );
}

function PremiumRateCard({
  settings,
  suggestion,
  suggestionError,
  onSaved,
}: {
  settings: SettingsRead;
  suggestion: PremiumSuggestionResponse | null;
  suggestionError: boolean;
  onSaved: () => void;
}) {
  const [rateInput, setRateInput] = useState<string>(
    () => (parseFloat(settings.premium_rate) * 100).toString(),
  );
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: async (body: components["schemas"]["SettingsUpdateRequest"]) => {
      const { data, error: apiError } = await api.PATCH("/api/v1/settings", { body });
      if (!data) throw new Error(extractDetail(apiError) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setSuccess(true);
      setError(null);
      setConfirming(false);
      setReason("");
      onSaved();
    },
    onError: (err: Error) => {
      setError(err.message);
      setSuccess(false);
    },
  });

  function handleAdopt() {
    if (!suggestion) return;
    const suggestedPct = (parseFloat(suggestion.suggested_rate) * 100).toString();
    setRateInput(suggestedPct);
  }

  function handleSave() {
    const rateStr = parseRateInput(rateInput);
    if (rateStr === null) {
      setError("溢價率請輸入有效百分比數字");
      return;
    }
    const clamped = clampRate(rateStr, settings.premium_rate_min, settings.premium_rate_max);
    if (clamped === settings.premium_rate) {
      setSuccess(true);
      return;
    }
    // Money-level change: require confirmation
    setConfirming(true);
    setError(null);
  }

  function handleConfirm() {
    const rateStr = parseRateInput(rateInput);
    if (rateStr === null) return;
    const clamped = clampRate(rateStr, settings.premium_rate_min, settings.premium_rate_max);
    const body: components["schemas"]["SettingsUpdateRequest"] = {
      premium_rate: clamped,
    };
    if (reason.trim()) {
      body.premium_change_reason = reason.trim();
    }
    mutation.mutate(body);
  }

  const cv = suggestion?.constraint_values as Record<string, unknown> | undefined;
  const wm = suggestion?.window_metrics as Record<string, unknown> | undefined;

  return (
    <div className="card">
      <h2>溢價率設定</h2>
      <div className="settings-premium-info">
        <div className="stat">
          <span className="field-label">目前溢價率</span>
          <span className="money">{formatPct(settings.premium_rate)}</span>
        </div>
        <div className="stat">
          <span className="field-label">允許範圍</span>
          <span>
            {formatPct(settings.premium_rate_min)} ~ {formatPct(settings.premium_rate_max)}
          </span>
        </div>
      </div>

      {suggestionError && (
        <p role="alert" className="form-error">
          讀取當日建議值失敗，請稍後再試（非無資料）
        </p>
      )}

      {!suggestionError && suggestion !== null && (
        <div className="settings-suggestion">
          {suggestion.insufficient_data ? (
            <p className="hint">資料不足，採用預設值</p>
          ) : (
            <>
              <div className="stat">
                <span className="field-label">當日建議值</span>
                <span className="money">{formatPct(suggestion.suggested_rate)}</span>
              </div>
              {cv && (
                <div className="settings-constraints">
                  <p className="hint">各約束中間值摘要：</p>
                  <ul>
                    <li>
                      毛利約束上限 (p_max1)：{cv.p_max1 != null ? formatPct(String(cv.p_max1)) : "N/A"}
                    </li>
                    <li>
                      負債約束上限 (p_max2)：{cv.p_max2 != null ? formatPct(String(cv.p_max2)) : "N/A"}
                      {cv.p_max2_note ? ` (${String(cv.p_max2_note)})` : ""}
                    </li>
                    <li>
                      選用率導向值：
                      {cv.take_rate_directional != null
                        ? formatPct(String(cv.take_rate_directional))
                        : "N/A"}
                    </li>
                  </ul>
                  {wm && typeof wm.liability_ratio === "number" && (
                    <p className="hint">
                      負債比：{wm.liability_ratio.toFixed(2)}
                    </p>
                  )}
                </div>
              )}
              <button type="button" className="btn-ghost" onClick={handleAdopt}>
                採納建議值
              </button>
            </>
          )}
        </div>
      )}

      <label className="field">
        <span className="field-label">溢價率 (%)</span>
        <input
          inputMode="decimal"
          value={rateInput}
          onChange={(e) => {
            setRateInput(e.target.value);
            setSuccess(false);
          }}
        />
      </label>

      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {success && <p className="form-success">溢價率已儲存</p>}

      {!confirming ? (
        <button type="button" className="btn-primary" onClick={handleSave}>
          儲存溢價率
        </button>
      ) : (
        <div className="settings-confirm-dialog">
          <p className="settings-confirm-title">確認變更溢價率</p>
          <p className="hint">
            溢價率為金錢級設定，變更將影響後續所有購物金撥款。
          </p>
          <label className="field">
            <span className="field-label">變更原因（選填）</span>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="settings-confirm-actions">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setConfirming(false);
                setReason("");
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={mutation.isPending}
              onClick={handleConfirm}
            >
              確認
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 區塊載入失敗時用：明確顯示「讀取失敗」，避免把錯誤狀態誤呈現為「無資料/空」。 */
function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      <p role="alert" className="form-error">
        {message}
      </p>
    </div>
  );
}

function PremiumHistoryCard({ history }: { history: PremiumRateHistoryRead[] }) {
  return (
    <div className="card">
      <h2>溢價率變更紀錄</h2>
      {history.length === 0 ? (
        <p className="hint">尚無變更紀錄</p>
      ) : (
        <table className="settings-history-table">
          <thead>
            <tr>
              <th>時間</th>
              <th>舊值</th>
              <th>新值</th>
              <th>當時建議值</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id}>
                <td>{new Date(h.changed_at).toLocaleString("zh-TW")}</td>
                <td>{formatPct(h.old_rate)}</td>
                <td>{formatPct(h.new_rate)}</td>
                <td>{h.suggested_rate_at_change ? formatPct(h.suggested_rate_at_change) : "N/A"}</td>
                <td>{h.reason ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const queryClient = useQueryClient();

  // 注意：GET /settings 與 premium-suggestion 皆為 clerk 可讀（POS/收購需讀稅率/溢價率），
  // 不能拿來把關。權限以「唯一的 MANAGER-only 端點」溢價率歷史為準（見下 historyQuery）。
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (!data) throw new Error(extractDetail(error) ?? "讀取設定失敗");
      return data;
    },
  });

  const suggestionQuery = useQuery({
    queryKey: ["premium-suggestion"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/store-credit/premium-suggestion/today");
      if (!data) throw new Error(extractDetail(error) ?? "讀取建議值失敗");
      return data;
    },
  });

  // 不以 token 的 role 把關（永不過期 token 的 role claim 可能過時）：改以後端授權為準。
  // 溢價率歷史是本頁唯一的 MANAGER-only 端點，故以它的 401/403 作為「需管理者權限」判準。
  const historyQuery = useQuery({
    queryKey: ["premium-rate-history"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/settings/premium-rate/history");
      if (response.status === 401 || response.status === 403) throw new ForbiddenError();
      if (!data) throw new Error(extractDetail(error) ?? "讀取溢價率歷史失敗");
      return data;
    },
    retry: false, // gate 查詢：權限/錯誤即時決斷，不重試（403 立即顯示提示）
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    void queryClient.invalidateQueries({ queryKey: ["premium-suggestion"] });
    void queryClient.invalidateQueries({ queryKey: ["premium-rate-history"] });
  }

  // 以 historyQuery.isFetching（含背景重新驗證）把關：有前一身分的快取歷史時 isPending 為
  // false，但仍在 refetch——若以 isPending 把關會先渲染快取設定/歷史才等到 403。
  if (settingsQuery.isPending || historyQuery.isFetching) return <p>載入中...</p>;
  if (historyQuery.error instanceof ForbiddenError) {
    return (
      <section>
        <h1 className="page-title">設定</h1>
        <p className="hint">需管理者權限</p>
      </section>
    );
  }
  if (settingsQuery.isError) {
    return (
      <p role="alert" className="form-error">
        {settingsQuery.error.message}
      </p>
    );
  }

  const settings = settingsQuery.data;

  return (
    <section>
      <h1 className="page-title">設定</h1>
      <div className="card-stack">
        <GeneralSettingsCard settings={settings} onSaved={refresh} />
        <PremiumRateCard
          settings={settings}
          suggestion={suggestionQuery.data ?? null}
          suggestionError={suggestionQuery.isError}
          onSaved={refresh}
        />
        {/* 歷史載入失敗（非權限，權限已於上方 gate 處理）時明確顯示錯誤，不可呈現為空白稽核紀錄 */}
        {historyQuery.isError ? (
          <ErrorCard title="溢價率變更紀錄" message="讀取變更紀錄失敗，請稍後再試" />
        ) : (
          <PremiumHistoryCard history={historyQuery.data ?? []} />
        )}
      </div>
    </section>
  );
}
