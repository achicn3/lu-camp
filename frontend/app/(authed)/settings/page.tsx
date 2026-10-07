"use client";
// /settings 管理者設定頁（docs/10 §5 /settings + docs/16 §6）：依用途分區、左側分區目錄；
// 每區一個儲存鈕（PATCH 僅送變更欄位）、改過未存有提示、離開頁面前提醒（2026-10-08 改版）。
// 溢價率為金錢級設定，二次確認。
import "./settings.css";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";

import {
  addTable,
  removeTable,
  sameTables,
} from "@/features/settings/dineInTables";
import { clampRate, formatPct, parseRateInput, ratePercentValue } from "@/features/settings/helpers";
import {
  AdvancedForm,
  InvoiceTaxForm,
  PricingForm,
  StoreCreditBasicsForm,
} from "@/features/settings/SettingsForms";
import { useDialogFocus } from "@/features/common/useDialogFocus";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";

type SettingsRead = components["schemas"]["SettingsRead"];
type PremiumSuggestionResponse = components["schemas"]["PremiumSuggestionResponse"];
type PremiumRateHistoryRead = components["schemas"]["PremiumRateHistoryRead"];
type SignatureRetentionReportItem =
  components["schemas"]["SignatureRetentionReportItem"];
type AgreementText = components["schemas"]["AgreementTextRead"];

/** 後端回 401/403 時用以標記「無權限」，與一般讀取失敗區分（驅動「需管理者權限」提示）。 */
class ForbiddenError extends Error {}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function MobilePaymentCard({
  settings,
  onSaved,
}: {
  settings: SettingsRead;
  onSaved: () => void;
}) {
  // 行動支付（docs/30）：LINE Pay 啟用開關＋各方式手續費率（店家成本，非向客人收取）。
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

    const linepayEnabled = form.get("linepay_enabled") === "on";
    const linepayRate = parseRateInput(String(form.get("linepay_fee_pct") ?? ""));
    if (linepayRate === null) {
      setError("LINE Pay 手續費率請輸入有效百分比數字");
      return;
    }
    const taiwanpayRate = parseRateInput(String(form.get("taiwanpay_fee_pct") ?? ""));
    if (taiwanpayRate === null) {
      setError("台灣Pay 手續費率請輸入有效百分比數字");
      return;
    }

    // 只送有變更的欄位（數值比較，避免 "0.02"→"0.0200" 字串不等誤判為變更）。
    const body: components["schemas"]["SettingsUpdateRequest"] = {};
    if (linepayEnabled !== settings.linepay_enabled) body.linepay_enabled = linepayEnabled;
    if (parseFloat(linepayRate) !== parseFloat(settings.linepay_fee_pct))
      body.linepay_fee_pct = linepayRate;
    if (parseFloat(taiwanpayRate) !== parseFloat(settings.taiwanpay_fee_pct))
      body.taiwanpay_fee_pct = taiwanpayRate;

    if (Object.keys(body).length === 0) {
      setSuccess(true);
      return;
    }
    mutation.mutate(body);
  }

  const linepayPct = ratePercentValue(settings.linepay_fee_pct);
  const taiwanpayPct = ratePercentValue(settings.taiwanpay_fee_pct);

  return (
    <form className="card" onSubmit={onSubmit}>
      <h3>行動支付</h3>
      <label className="field field-toggle">
        <input
          type="checkbox"
          name="linepay_enabled"
          defaultChecked={settings.linepay_enabled}
        />
        <span className="field-label">收 LINE Pay（店家掃客人手機上的付款條碼）</span>
      </label>
      <label className="field">
        <span className="field-label">LINE Pay 手續費率 (%)</span>
        <input name="linepay_fee_pct" inputMode="decimal" defaultValue={linepayPct} required />
      </label>
      <label className="field">
        <span className="field-label">台灣Pay 手續費率 (%)</span>
        <input name="taiwanpay_fee_pct" inputMode="decimal" defaultValue={taiwanpayPct} required />
        <span className="hint">
          手續費為店家負擔的成本、不向客人加收；行動支付款項不計入現金抽屜（關帳另列）。
        </span>
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {success && <p className="form-success">行動支付設定已儲存</p>}
      <button type="submit" className="btn-primary" disabled={mutation.isPending}>
        儲存行動支付設定
      </button>
    </form>
  );
}

function DineInCard({
  settings,
  onSaved,
}: {
  settings: SettingsRead;
  onSaved: () => void;
}) {
  // 餐飲內用（docs/35）：桌號清單（順序即 POS 按鈕順序）＋出餐單開關。
  // 清單空＝尚未維護，此時 POS 不讓選內用（fail closed，不讓店員自由打字繞過）。
  const [tables, setTables] = useState<string[]>(settings.dine_in_tables);
  const [draft, setDraft] = useState("");
  const [printTicket, setPrintTicket] = useState(settings.print_kitchen_ticket);
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

  function handleAdd() {
    setSuccess(false);
    const result = addTable(tables, draft);
    setError(result.error);
    if (result.error === null) {
      setTables(result.tables);
      setDraft("");
    }
  }

  function handleSave() {
    setError(null);
    setSuccess(false);
    const body: components["schemas"]["SettingsUpdateRequest"] = {};
    if (!sameTables(tables, settings.dine_in_tables)) body.dine_in_tables = tables;
    if (printTicket !== settings.print_kitchen_ticket) body.print_kitchen_ticket = printTicket;
    if (Object.keys(body).length === 0) {
      setSuccess(true);
      return;
    }
    mutation.mutate(body);
  }

  return (
    <div className="card dinein-card">
      <h3>內用桌號與出餐單</h3>
      <div className="field">
        <span className="field-label">桌號清單</span>
        {tables.length === 0 ? (
          <p className="hint">還沒設定桌號。設定之前，POS 不能選「內用」。</p>
        ) : (
          <ul className="dinein-table-list">
            {tables.map((table) => (
              <li key={table}>
                <span>{table}</span>
                <button
                  type="button"
                  className="btn-ghost"
                  aria-label={`移除桌號 ${table}`}
                  onClick={() => {
                    setSuccess(false);
                    setTables(removeTable(tables, table));
                  }}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="dinein-table-add">
          <input
            aria-label="新增桌號"
            value={draft}
            placeholder="例如 A1"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAdd();
              }
            }}
          />
          <button type="button" className="btn-ghost" onClick={handleAdd}>
            新增桌號
          </button>
        </div>
        <span className="hint">
          這裡的排列順序就是 POS 上按鈕的順序。移除桌號只影響之後能選的桌號，已經結帳的交易不受影響。
        </span>
      </div>
      <label className="field field-toggle">
        <input
          type="checkbox"
          checked={printTicket}
          onChange={(e) => {
            setSuccess(false);
            setPrintTicket(e.target.checked);
          }}
        />
        <span className="field-label">結帳後自動列印出餐單（桌號＋餐飲品項）</span>
      </label>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {success && <p className="form-success">餐飲內用設定已儲存</p>}
      <button
        type="button"
        className="btn-primary"
        disabled={mutation.isPending}
        onClick={handleSave}
      >
        儲存餐飲內用設定
      </button>
    </div>
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
    () => ratePercentValue(settings.premium_rate),
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
    const suggestedPct = ratePercentValue(suggestion.suggested_rate);
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
      <h3>購物金溢價率</h3>
      <p className="hint">
        客人收購選購物金時，比現金多給的比例。例：溢價率 6%，收購價 $1,000 選購物金拿 $1,060。
      </p>
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
                <details className="settings-constraints">
                  <summary>為什麼建議這個數字</summary>
                  <ul>
                    <li>
                      毛利撐得住的上限：
                      {cv.p_max1 != null ? formatPct(String(cv.p_max1)) : "資料不足，先不算"}
                    </li>
                    <li>
                      手上現金撐得住的上限：
                      {cv.p_max2 != null
                        ? formatPct(String(cv.p_max2))
                        : String(cv.p_max2_note ?? "").includes("monthly_fixed_cash_outflow")
                          ? "還沒填「每月固定現金支出」，這項先不算"
                          : "資料不足，先不算"}
                    </li>
                    <li>
                      依客人選購物金的比例調整：
                      {cv.take_rate_directional != null
                        ? formatPct(String(cv.take_rate_directional))
                        : "資料不足，先不算"}
                    </li>
                  </ul>
                  {wm && typeof wm.liability_ratio === "number" && (
                    <p className="hint">
                      目前流通中的購物金約是每月固定支出的 {wm.liability_ratio.toFixed(2)} 倍。
                    </p>
                  )}
                </details>
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
// 贈品／折扣原因代碼管理。**停用不實刪**：歷史單據引用過的原因不能因為後台刪掉就消失
// （單據另存名稱快照），停用只是讓它不再出現在 POS 選單。code 建立後不可改——報表以它
// 對照分類，改了會讓同一件事在報表上斷成兩段。
function ReasonCard({
  title,
  kind,
}: {
  title: string;
  kind: "gift-reasons" | "discount-reasons";
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [requiresNote, setRequiresNote] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["reasons", kind],
    queryFn: async () => {
      const { data, error: apiError } = await api.GET(
        kind === "gift-reasons" ? "/api/v1/gift-reasons" : "/api/v1/discount-reasons",
        { params: { query: { include_inactive: true } } },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "讀取原因清單失敗");
      return data;
    },
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["reasons", kind] });
  }

  const create = useMutation({
    mutationFn: async () => {
      const { data, error: apiError } = await api.POST(
        kind === "gift-reasons" ? "/api/v1/gift-reasons" : "/api/v1/discount-reasons",
        {
          body: {
            // 代號由系統產生：後端要求 ^[A-Z0-9_]+$，但那是給程式辨識用的，
            // 沒有理由逼台灣門市的店員自己想一個英文字。店員只填中文名稱。
            code: `R${Date.now().toString(36).toUpperCase()}`,
            name: name.trim(),
            requires_note: requiresNote,
            sort_order: 0,
          },
        },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "新增原因失敗");
      return data;
    },
    onSuccess: () => {
      setName("");
      setRequiresNote(false);
      setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const toggle = useMutation({
    mutationFn: async (row: { id: number; is_active: boolean }) => {
      const { data, error: apiError } = await api.PATCH(
        kind === "gift-reasons"
          ? "/api/v1/gift-reasons/{reason_id}"
          : "/api/v1/discount-reasons/{reason_id}",
        {
          params: { path: { reason_id: row.id } },
          body: { is_active: !row.is_active },
        },
      );
      if (!data) throw new Error(extractDetail(apiError) ?? "更新原因失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const rows = listQuery.data ?? [];

  return (
    <div className="card">
      <h3>{title}</h3>
      <p className="hint">
        停用的原因不再出現在 POS 的選單裡，但先前的單據仍會保留當初選的名稱。
      </p>
      {listQuery.isError && (
        <p role="alert" className="form-error">
          {listQuery.error.message}
        </p>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>名稱</th>
            <th>備註必填</th>
            <th>狀態</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={4} className="hint">
                尚未建立任何原因。
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.requires_note ? "是" : "否"}</td>
                <td>{row.is_active ? "啟用中" : "已停用"}</td>
                <td>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={toggle.isPending}
                    onClick={() => toggle.mutate(row)}
                  >
                    {row.is_active ? "停用" : "重新啟用"}
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="reason-add">
        <label className="field">
          <span>名稱</span>
          <input
            value={name}
            maxLength={50}
            onChange={(e) => setName(e.target.value)}
            placeholder="例：試用品"
          />
        </label>
        <label className="field field-toggle">
          <input
            type="checkbox"
            checked={requiresNote}
            onChange={(e) => setRequiresNote(e.target.checked)}
          />
          <span>選用時必須填備註</span>
        </label>
        <button
          type="button"
          className="btn-primary"
          disabled={create.isPending || name.trim() === ""}
          onClick={() => create.mutate()}
        >
          新增原因
        </button>
      </div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}

function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <p role="alert" className="form-error">
        {message}
      </p>
    </div>
  );
}

function PremiumHistoryCard({ history }: { history: PremiumRateHistoryRead[] }) {
  return (
    <details className="card settings-history">
      <summary>
        <h3>溢價率變更紀錄（{history.length} 筆）</h3>
      </summary>
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
                <td>{formatTaipeiDateTime(h.changed_at)}</td>
                <td>{formatPct(h.old_rate)}</td>
                <td>{formatPct(h.new_rate)}</td>
                <td>{h.suggested_rate_at_change ? formatPct(h.suggested_rate_at_change) : "無"}</td>
                <td>{h.reason ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

function SignatureRetentionReportCard({
  rows,
}: {
  rows: SignatureRetentionReportItem[];
}) {
  return (
    <div className="card">
      <h3>可清除的簽名圖檔</h3>
      <p className="hint">
        這裡只列出可以清除的簽名圖檔，系統不會自動刪除，要不要刪由店長決定。
        清除的只有圖檔本身；簽署紀錄、簽署內容與時間都會完整保留，不受保留天數影響。
      </p>
      {rows.length === 0 ? (
        <p className="hint">目前沒有超過保留天數的簽名圖檔。</p>
      ) : (
        <table className="settings-history-table">
          <thead>
            <tr>
              <th>任務</th>
              <th>類型</th>
              <th>簽署時間</th>
              <th>到期時間</th>
              <th>報表列入時間</th>
              <th>影像</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.task_id}>
                <td>#{row.task_id}</td>
                <td>
                  {row.kind === "STORE_CREDIT_USE"
                    ? "購物金使用"
                    : row.kind === "ACQUISITION_AFFIDAVIT"
                      ? "收購切結"
                      : "交易簽收"}
                </td>
                <td>{formatTaipeiDateTime(row.signed_at)}</td>
                <td>{formatTaipeiDateTime(row.retention_until)}</td>
                <td>{formatTaipeiDateTime(row.reported_at)}</td>
                <td>{row.signature_png_retained ? "仍保留" : "已移除"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}


// -- 收購切結書內文（店家可自行修改；改內容＝發新版本，舊版不動）--
function AgreementCard() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  const agreementQuery = useQuery({
    queryKey: ["agreement", "current"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/agreements/current");
      if (!data) throw new Error(extractDetail(error) ?? "讀取切結書失敗");
      return data;
    },
    retry: false,
  });

  const current = agreementQuery.data ?? null;
  return (
    <div className="card">
      <h3>收購切結書</h3>
      <p className="hint">
        客人在手持裝置上簽的就是這份全文。改了內容會存成新版本，先前簽過的簽名仍對應
        他當初看到的那一份，不會被改掉。
      </p>
      {agreementQuery.isError && (
        <p role="alert" className="form-error">
          {agreementQuery.error.message}
        </p>
      )}
      {current !== null && (
        <>
          <dl className="agreement-meta">
            <div>
              <dt>目前版本</dt>
              <dd>第 {current.version} 版</dd>
            </div>
            <div>
              <dt>最後更新</dt>
              <dd>{formatTaipeiDateTime(current.created_at)}</dd>
            </div>
          </dl>
          <p className="agreement-title-preview">{current.title}</p>
          <div className="kiosk-agreement-body agreement-preview">{current.body}</div>
          <div className="agreement-card-actions">
            <button type="button" className="btn-primary" onClick={() => setEditing(true)}>
              編輯切結書內容
            </button>
            <button type="button" className="btn-ghost" onClick={() => setPreviewing(true)}>
              整份預覽
            </button>
          </div>
        </>
      )}
      {previewing && current !== null && (
        <AgreementPreviewDialog
          title={current.title}
          body={current.body}
          onClose={() => setPreviewing(false)}
        />
      )}
      {editing && current !== null && (
        <AgreementEditDialog
          current={current}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void queryClient.invalidateQueries({ queryKey: ["agreement"] });
          }}
        />
      )}
    </div>
  );
}

function AgreementEditDialog({
  current,
  onClose,
  onSaved,
}: {
  current: AgreementText;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(current.title);
  const [body, setBody] = useState(current.body);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.POST("/api/v1/agreements", {
        // 帶開啟視窗時看到的版本：中間若有人改過，後端回 409 而不是無聲蓋掉對方的內容
        body: { title, body, expected_version: current.version },
      });
      if (!data) throw new Error(extractDetail(err) ?? "儲存失敗");
      return data;
    },
    onSuccess: onSaved,
    onError: (err: Error) => setError(err.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (title.trim() === "") {
      setError("標題不可空白");
      return;
    }
    if (body.trim() === "") {
      setError("內文不可空白");
      return;
    }
    save.mutate();
  }

  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label="編輯切結書內容">
      <form className="card pos-dialog agreement-dialog" onSubmit={submit}>
        <h2>編輯切結書內容</h2>
        <p className="hint">
          儲存後會成為第 {current.version + 1} 版，之後的收購都用新版；已簽過的不受影響。
        </p>
        <label className="field">
          <span className="field-label">標題</span>
          <input
            value={title}
            maxLength={100}
            aria-label="切結書標題"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">內文</span>
          <textarea
            className="agreement-textarea"
            value={body}
            rows={16}
            maxLength={20000}
            aria-label="切結書內文"
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <p className="hint">{body.length} / 20000 字</p>
        {/* 小預覽只夠瞄一眼（限高＋捲動）；要確認排版請開整份預覽。 */}
        <div className="agreement-card-actions">
          <p className="field-label">手持裝置上的樣子</p>
          <button type="button" className="btn-ghost" onClick={() => setPreviewing(true)}>
            整份預覽
          </button>
        </div>
        <p className="agreement-title-preview">{title}</p>
        <div className="kiosk-agreement-body agreement-preview">{body}</div>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={save.isPending}>
            {save.isPending ? "儲存中…" : "儲存"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose} disabled={save.isPending}>
            取消
          </button>
        </div>
      </form>
      {previewing && (
        // 預覽**編輯中**的內容（不是已存檔的舊版），關掉後編輯視窗與內容都還在。
        <AgreementPreviewDialog
          title={title}
          body={body}
          onClose={() => setPreviewing(false)}
        />
      )}
    </div>
  );
}


// 整份預覽：卡片與編輯視窗裡的預覽都被限高（180/260px），店主永遠看不到完整一份，
// 又是巢狀捲動。這裡把整份攤開，寬度比照手持裝置，讓他真的能檢查排版。
function AgreementPreviewDialog({
  title,
  body,
  onClose,
}: {
  title: string;
  body: string;
  onClose: () => void;
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>();
  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="切結書預覽"
      ref={dialogRef}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
    >
      <div className="card pos-dialog agreement-full-dialog">
        <h2>切結書預覽</h2>
        <p className="hint">
          客人在手持裝置上看到的就是這一份。裝置畫面較小，內文區會自己捲動——這裡把整份
          攤開讓你一次看完。
        </p>
        <div className="agreement-full-sheet">
          <p className="agreement-full-title">{title}</p>
          {/* 刻意不用 .kiosk-agreement-body：那個 class 帶 260px 限高與捲動，
              預覽要的是「一次看完」。排版規則（pre-wrap、強制斷行）與手持端一致。 */}
          <div className="agreement-full-body">{body}</div>
        </div>
        <div className="pos-dialog-actions">
          <button type="button" className="btn-primary" onClick={onClose}>
            關閉
          </button>
        </div>
      </div>
    </div>
  );
}

// -- 開店前檢查項目 --
// 店主自己決定每天開店要確認什麼；系統自動檢查的（開帳、各機器連線）不在這裡，那些不能手動打勾。
function OpeningCheckItemsCard() {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [href, setHref] = useState("");
  const [error, setError] = useState<string | null>(null);

  const todayQuery = useQuery({
    queryKey: ["opening-check", "today"],
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/opening-check/today");
      if (!data) throw new Error(extractDetail(err) ?? "讀取檢查項目失敗");
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.POST("/api/v1/opening-check/items", {
        body: { label: label.trim(), href: href.trim() === "" ? null : href.trim() },
      });
      if (!data) throw new Error(extractDetail(err) ?? "新增失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      setLabel("");
      setHref("");
      void queryClient.invalidateQueries({ queryKey: ["opening-check"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const { error: err, response } = await api.DELETE(
        "/api/v1/opening-check/items/{item_id}",
        { params: { path: { item_id: id } } },
      );
      if (!response.ok) throw new Error(extractDetail(err) ?? "刪除失敗");
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["opening-check"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const items = todayQuery.data?.items ?? [];
  return (
    <div className="card">
      <h3>開店前檢查項目</h3>
      <p className="hint">
        這些會出現在每天的「開店前檢查」頁，由店員逐項確認。開帳與各機器的連線狀態由系統
        自動判斷，不需要也不能在這裡加。
      </p>
      <table className="inv-table">
        <thead>
          <tr>
            <th>項目</th>
            <th>點「前往處理」要去哪（選填）</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.label}</td>
              <td>{item.href === null || item.href === "" ? "—" : item.href}</td>
              <td>
                <button
                  type="button"
                  className="btn-ghost btn-danger-text"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(item.id)}
                >
                  刪除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {todayQuery.isSuccess && items.length === 0 && <p className="hint">目前沒有自訂項目。</p>}
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <form
        className="opening-add-form"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          if (label.trim() === "") {
            setError("請輸入項目名稱");
            return;
          }
          create.mutate();
        }}
      >
        <label className="field">
          <span className="field-label">新增項目</span>
          <input
            value={label}
            aria-label="新增項目"
            maxLength={100}
            placeholder="例如：招牌燈打開"
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">連結（選填）</span>
          <input
            value={href}
            aria-label="連結"
            maxLength={200}
            placeholder="例如：/cash"
            onChange={(e) => setHref(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={create.isPending}>
          {create.isPending ? "新增中…" : "新增"}
        </button>
      </form>
    </div>
  );
}

/** 分區目錄：key 是區塊 id（也是表單回報「未儲存」用的 key）。 */
const SECTIONS: { id: string; title: string }[] = [
  { id: "invoice", title: "發票與稅" },
  { id: "pricing", title: "收購與定價" },
  { id: "store-credit", title: "購物金" },
  { id: "payments", title: "付款方式" },
  { id: "dine-in", title: "餐飲" },
  { id: "pos", title: "POS 選項" },
  { id: "opening", title: "開店前檢查" },
  { id: "advanced", title: "其他" },
];

const LEAVE_WARNING = "有設定還沒儲存，確定要離開這一頁嗎？";

function SettingsSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={`settings-${id}`} className="settings-section" aria-labelledby={`settings-${id}-title`}>
      <h2 id={`settings-${id}-title`} className="settings-section-title">
        {title}
      </h2>
      <div className="card-stack">{children}</div>
    </section>
  );
}

function SettingsLayout({
  dirtySections,
  children,
}: {
  dirtySections: ReadonlySet<string>;
  children: ReactNode;
}) {
  const anyDirty = dirtySections.size > 0;

  // 有沒存的變更就提醒：重新整理／關分頁（beforeunload），以及點選單切到別頁
  // （Next 的站內連結不會觸發 beforeunload，要在點擊時攔）。
  useEffect(() => {
    if (!anyDirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const onClick = (event: MouseEvent) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      if (!(link instanceof HTMLAnchorElement)) return;
      const href = link.getAttribute("href") ?? "";
      if (href.startsWith("#") || link.target === "_blank") return;
      if (!window.confirm(LEAVE_WARNING)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [anyDirty]);

  return (
    <section className="settings-page">
      <header className="settings-header">
        <h1 className="page-title">設定</h1>
        {anyDirty && (
          <p className="settings-dirty" role="status">
            有設定還沒儲存
          </p>
        )}
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="設定分區">
          <ul>
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <a href={`#settings-${section.id}`}>
                  {section.title}
                  {dirtySections.has(section.id) && (
                    <span className="settings-nav-dot" aria-label="有未儲存的變更" />
                  )}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="settings-sections">{children}</div>
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [dirtySections, setDirtySections] = useState<ReadonlySet<string>>(() => new Set());
  const reportDirty = useCallback((key: string, dirty: boolean) => {
    setDirtySections((prev) => {
      if (prev.has(key) === dirty) return prev;
      const next = new Set(prev);
      if (dirty) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);

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
  const retentionReportQuery = useQuery({
    queryKey: ["signature-retention-report"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/signing/retention-report", {
        params: { query: { limit: 200 } },
      });
      if (!data) throw new Error(extractDetail(error) ?? "讀取簽名待清理報表失敗");
      return data;
    },
  });

  function refreshSettings() {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    void queryClient.invalidateQueries({ queryKey: ["premium-suggestion"] });
  }

  function refreshPremiumSettings() {
    refreshSettings();
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
  const premium = (
    <PremiumRateCard
      settings={settings}
      suggestion={suggestionQuery.data ?? null}
      suggestionError={suggestionQuery.isError}
      onSaved={refreshPremiumSettings}
    />
  );

  return (
    <SettingsLayout dirtySections={dirtySections}>
      <SettingsSection id="invoice" title="發票與稅">
        <InvoiceTaxForm settings={settings} onSaved={refreshSettings} onDirtyChange={reportDirty} />
      </SettingsSection>
      <SettingsSection id="pricing" title="收購與定價">
        <PricingForm settings={settings} onSaved={refreshSettings} onDirtyChange={reportDirty} />
        <AgreementCard />
      </SettingsSection>
      <SettingsSection id="store-credit" title="購物金">
        <StoreCreditBasicsForm
          settings={settings}
          onSaved={refreshPremiumSettings}
          onDirtyChange={reportDirty}
        />
        {premium}
        {/* 歷史載入失敗（非權限，權限已於上方 gate 處理）時明確顯示錯誤，不可呈現為空白稽核紀錄 */}
        {historyQuery.isError ? (
          <ErrorCard title="溢價率變更紀錄" message="讀取變更紀錄失敗，請稍後再試" />
        ) : (
          <PremiumHistoryCard history={historyQuery.data ?? []} />
        )}
      </SettingsSection>
      <SettingsSection id="payments" title="付款方式">
        <MobilePaymentCard settings={settings} onSaved={refreshSettings} />
      </SettingsSection>
      <SettingsSection id="dine-in" title="餐飲">
        <DineInCard settings={settings} onSaved={refreshSettings} />
      </SettingsSection>
      <SettingsSection id="pos" title="POS 選項">
        <ReasonCard title="贈品原因" kind="gift-reasons" />
        <ReasonCard title="折扣原因" kind="discount-reasons" />
      </SettingsSection>
      <SettingsSection id="opening" title="開店前檢查">
        <OpeningCheckItemsCard />
      </SettingsSection>
      <SettingsSection id="advanced" title="其他">
        <AdvancedForm settings={settings} onSaved={refreshSettings} onDirtyChange={reportDirty} />
        {retentionReportQuery.isError ? (
          <ErrorCard title="可清除的簽名圖檔" message="讀取待清理報表失敗，請稍後再試" />
        ) : (
          <SignatureRetentionReportCard rows={retentionReportQuery.data ?? []} />
        )}
      </SettingsSection>
    </SettingsLayout>
  );
}
