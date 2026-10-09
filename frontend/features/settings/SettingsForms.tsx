"use client";
// 設定頁的分區表單（2026-10-08 改版）：原本「一般設定」一張卡塞十幾項、一頁七八顆儲存鈕，
// 改成依用途分區，每區一個儲存鈕＋「有未儲存的變更」提示。只改畫面與分組，不改設定的作用。
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { parsePctInput, parseRateInput, ratePercentValue } from "@/features/settings/helpers";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type SettingsRead = components["schemas"]["SettingsRead"];
type SettingsPatch = components["schemas"]["SettingsUpdateRequest"];

/** 送出前的檢查結果：要送的變更，或給店主看的錯誤訊息。 */
type BuildResult = { body: SettingsPatch } | { error: string };

export type DirtyReporter = (key: string, dirty: boolean) => void;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function ntdText(value: string): string {
  const n = parseNtd(value);
  return n !== null ? formatNtd(n) : "0";
}

/** 一張可儲存的設定表單：只送有變更的欄位，改過未存時提示，存好顯示成功。 */
export function SettingsForm({
  formKey,
  title,
  description,
  saveLabel,
  dirty,
  build,
  onSaved,
  onDirtyChange,
  children,
}: {
  formKey: string;
  title: string;
  description?: ReactNode;
  saveLabel: string;
  dirty: boolean;
  build: () => BuildResult;
  onSaved: () => void;
  onDirtyChange: DirtyReporter;
  children: ReactNode;
}) {
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // 「已儲存」只在沒有新的未存變更時顯示（見下方 success && !dirty），不必在 effect 裡清掉。
  useEffect(() => {
    onDirtyChange(formKey, dirty);
  }, [formKey, dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(formKey, false), [formKey, onDirtyChange]);

  const mutation = useMutation({
    mutationFn: async (body: SettingsPatch) => {
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
    const result = build();
    if ("error" in result) {
      setError(result.error);
      return;
    }
    if (Object.keys(result.body).length === 0) {
      setSuccess(true);
      return;
    }
    mutation.mutate(result.body);
  }

  return (
    <form className="card settings-card" onSubmit={onSubmit} aria-label={title}>
      <div className="settings-card-head">
        <h3>{title}</h3>
        {dirty && <span className="settings-dirty">有未儲存的變更</span>}
      </div>
      {description && <p className="hint">{description}</p>}
      <div className="settings-fields">{children}</div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {success && !dirty && <p className="form-success">已儲存</p>}
      <div className="settings-card-actions">
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? "儲存中…" : saveLabel}
        </button>
      </div>
    </form>
  );
}

/** 開關：標題＋一句「打開會怎樣」。仍是原生 checkbox（鍵盤、讀屏、測試都照舊）。 */
export function SettingsSwitch({
  name,
  label,
  description,
  checked,
  onChange,
}: {
  name: string;
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="settings-switch">
      <input
        type="checkbox"
        role="switch"
        name={name}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="settings-switch-track" aria-hidden="true" />
      <span className="settings-switch-text">
        <span className="settings-switch-title">{label}</span>
        {description && <span className="hint">{description}</span>}
      </span>
    </label>
  );
}

function NumberField({
  name,
  label,
  value,
  onChange,
  suffix,
  hint,
  inputMode = "numeric",
}: {
  name: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  suffix?: string;
  hint?: string;
  inputMode?: "numeric" | "decimal";
}) {
  return (
    <label className="field settings-number">
      <span className="field-label">{label}</span>
      <span className="settings-input-row">
        <input
          name={name}
          aria-label={label}
          inputMode={inputMode}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required
        />
        {suffix && <span className="settings-suffix">{suffix}</span>}
      </span>
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

// ── 發票與稅 ──

export function InvoiceTaxForm({
  settings,
  onSaved,
  onDirtyChange,
}: {
  settings: SettingsRead;
  onSaved: () => void;
  onDirtyChange: DirtyReporter;
}) {
  const initialTax = ratePercentValue(settings.tax_rate);
  const [einvoice, setEinvoice] = useState(settings.einvoice_enabled);
  const [tax, setTax] = useState(initialTax);
  const dirty =
    einvoice !== settings.einvoice_enabled || tax.trim() !== initialTax;

  function build(): BuildResult {
    const taxRate = parseRateInput(tax);
    if (taxRate === null) return { error: "稅率請輸入有效百分比數字" };
    const body: SettingsPatch = {};
    if (einvoice !== settings.einvoice_enabled) body.einvoice_enabled = einvoice;
    // 以數值比較：後端回 "0.0500"，畫面組回來可能是 "0.0500" 或 "0.05"，字串不等但值相同。
    if (parseFloat(taxRate) !== parseFloat(settings.tax_rate)) body.tax_rate = taxRate;
    return { body };
  }

  return (
    <SettingsForm
      formKey="invoice"
      title="電子發票"
      saveLabel="儲存發票設定"
      dirty={dirty}
      build={build}
      onSaved={onSaved}
      onDirtyChange={onDirtyChange}
    >
      <SettingsSwitch
        name="einvoice_enabled"
        label="開電子發票"
        description="打開後，每筆交易結帳時會自動開電子發票；關掉時交易照常記錄、之後可以補開。"
        checked={einvoice}
        onChange={setEinvoice}
      />
      <p className="hint">
        購物金＋其他付款時，發票扣掉購物金後開，品項金額照比例扣。例：$1,000 用購物金 $300 →
        發票開 $700。
      </p>
      <NumberField
        name="tax_rate"
        label="營業稅率"
        value={tax}
        onChange={setTax}
        suffix="%"
        inputMode="decimal"
      />
    </SettingsForm>
  );
}

// ── 收購與定價 ──

export function PricingForm({
  settings,
  onSaved,
  onDirtyChange,
}: {
  settings: SettingsRead;
  onSaved: () => void;
  onDirtyChange: DirtyReporter;
}) {
  const [margin, setMargin] = useState(String(settings.default_margin_pct));
  const [purchaseMargin, setPurchaseMargin] = useState(
    String(settings.purchase_default_margin_pct),
  );
  const [commission, setCommission] = useState(String(settings.default_commission_pct));
  const [affidavit, setAffidavit] = useState(settings.require_acquisition_affidavit);
  const [autoPrint, setAutoPrint] = useState(settings.auto_print_acquisition_labels);
  const dirty =
    margin.trim() !== String(settings.default_margin_pct) ||
    purchaseMargin.trim() !== String(settings.purchase_default_margin_pct) ||
    commission.trim() !== String(settings.default_commission_pct) ||
    affidavit !== settings.require_acquisition_affidavit ||
    autoPrint !== settings.auto_print_acquisition_labels;

  function build(): BuildResult {
    // 嚴格整數（"50.5"/"50abc" 擋下，不可前綴解析成 50 存錯值）。毛利 0–99（≥100 會除以零）。
    const m = parsePctInput(margin);
    if (m === null) return { error: "收購目標毛利請輸入 0-99 的整數" };
    const pm = parsePctInput(purchaseMargin);
    if (pm === null) return { error: "採購目標毛利請輸入 0-99 的整數" };
    const c = parsePctInput(commission, 100);
    if (c === null) return { error: "寄售抽成請輸入 0-100 的整數" };
    const body: SettingsPatch = {};
    if (m !== settings.default_margin_pct) body.default_margin_pct = m;
    if (pm !== settings.purchase_default_margin_pct) body.purchase_default_margin_pct = pm;
    if (c !== settings.default_commission_pct) body.default_commission_pct = c;
    if (affidavit !== settings.require_acquisition_affidavit)
      body.require_acquisition_affidavit = affidavit;
    if (autoPrint !== settings.auto_print_acquisition_labels)
      body.auto_print_acquisition_labels = autoPrint;
    return { body };
  }

  return (
    <SettingsForm
      formKey="pricing"
      title="收購預設值"
      saveLabel="儲存收購與定價"
      dirty={dirty}
      build={build}
      onSaved={onSaved}
      onDirtyChange={onDirtyChange}
    >
      <div className="settings-grid">
        <NumberField
          name="default_margin_pct"
        label="收購目標毛利"
          value={margin}
          onChange={setMargin}
          suffix="%"
          hint="折數鑑價先扣掉稅與支付手續費，再照這個毛利算收購價。"
        />
        <NumberField
          name="purchase_default_margin_pct"
        label="採購目標毛利"
          value={purchaseMargin}
          onChange={setPurchaseMargin}
          suffix="%"
          hint="採購建立商品時先帶的毛利，每件還能各自調。"
        />
        <NumberField
          name="default_commission_pct"
        label="寄售抽成"
          value={commission}
          onChange={setCommission}
          suffix="%"
          hint="寄售品賣出時店家抽幾成，收購時可以再改。"
        />
      </div>
      <SettingsSwitch
        name="require_acquisition_affidavit"
        label="收購付錢前一定要客人簽名"
        description="客人要先在顧客螢幕簽切結書才能付款（收購頁與排隊收購都適用）。關掉時可以不簽直接付。"
        checked={affidavit}
        onChange={setAffidavit}
      />
      <SettingsSwitch
        name="auto_print_acquisition_labels"
        label="收購送出後自動印標籤"
        description="櫃台沒接標籤機時可以關掉，之後再從收購紀錄補印。"
        checked={autoPrint}
        onChange={setAutoPrint}
      />
    </SettingsForm>
  );
}

// ── 購物金 ──

export function StoreCreditBasicsForm({
  settings,
  onSaved,
  onDirtyChange,
}: {
  settings: SettingsRead;
  onSaved: () => void;
  onDirtyChange: DirtyReporter;
}) {
  const initialMinSpend = ntdText(settings.store_credit_min_spend);
  const initialOutflow = ntdText(settings.monthly_fixed_cash_outflow);
  const [minSpend, setMinSpend] = useState(initialMinSpend);
  const [outflow, setOutflow] = useState(initialOutflow);
  // 以金額比較：畫面顯示 "60,000"，店主打 "60000" 是同一個數字，不算沒存。
  const dirty =
    parseNtd(minSpend) !== parseNtd(settings.store_credit_min_spend) ||
    parseNtd(outflow) !== parseNtd(settings.monthly_fixed_cash_outflow);

  function build(): BuildResult {
    const ms = parseNtd(minSpend);
    if (ms === null || ms < 0) return { error: "購物金低消門檻請輸入非負整數（0＝不限制）" };
    const out = parseNtd(outflow);
    if (out === null || out < 0) return { error: "每月固定現金支出請輸入非負整數" };
    const body: SettingsPatch = {};
    if (ms !== parseNtd(settings.store_credit_min_spend)) body.store_credit_min_spend = ms;
    if (out !== parseNtd(settings.monthly_fixed_cash_outflow))
      body.monthly_fixed_cash_outflow = out;
    return { body };
  }

  return (
    <SettingsForm
      formKey="store-credit"
      title="購物金使用"
      saveLabel="儲存購物金設定"
      dirty={dirty}
      build={build}
      onSaved={onSaved}
      onDirtyChange={onDirtyChange}
    >
      <div className="settings-grid">
        <NumberField
          name="store_credit_min_spend"
        label="購物金低消門檻"
          value={minSpend}
          onChange={setMinSpend}
          suffix="元"
          hint="整筆消費（餐飲也算）沒到這個金額就不能用購物金折抵；0＝不限制。"
        />
        <NumberField
          name="monthly_fixed_cash_outflow"
        label="每月固定現金支出"
          value={outflow}
          onChange={setOutflow}
          suffix="元"
          hint="房租、薪水這類每月一定要付的現金。只用來估算下面的建議溢價率。"
        />
      </div>
    </SettingsForm>
  );
}

// ── 進階 ──

export function AdvancedForm({
  settings,
  onSaved,
  onDirtyChange,
}: {
  settings: SettingsRead;
  onSaved: () => void;
  onDirtyChange: DirtyReporter;
}) {
  // 滾動部署／舊測試資料可能還沒有這欄：與後端預設一致（半年）。
  const retentionCurrent = String(settings.signature_png_retention_days ?? 183);
  const [retention, setRetention] = useState(retentionCurrent);
  const [clerkCategories, setClerkCategories] = useState(settings.allow_clerk_manage_categories);
  const dirty =
    retention.trim() !== retentionCurrent ||
    clerkCategories !== settings.allow_clerk_manage_categories;

  function build(): BuildResult {
    const days = Number(retention.trim());
    if (!/^\d+$/.test(retention.trim()) || days < 1 || days > 3650) {
      return { error: "簽名圖檔保留天數請輸入 1 到 3650 之間的整數" };
    }
    const body: SettingsPatch = {};
    if (String(days) !== retentionCurrent) body.signature_png_retention_days = days;
    if (clerkCategories !== settings.allow_clerk_manage_categories)
      body.allow_clerk_manage_categories = clerkCategories;
    return { body };
  }

  return (
    <SettingsForm
      formKey="advanced"
      title="權限與資料保存"
      saveLabel="儲存其他設定"
      dirty={dirty}
      build={build}
      onSaved={onSaved}
      onDirtyChange={onDirtyChange}
    >
      <SettingsSwitch
        name="allow_clerk_manage_categories"
        label="店員可以管理商品分類"
        description="打開後店員也能新增、改名分類；關掉時只有店長可以。"
        checked={clerkCategories}
        onChange={setClerkCategories}
      />
      <NumberField
        name="signature_png_retention_days"
        label="簽名圖檔保留天數"
        value={retention}
        onChange={setRetention}
        suffix="天"
        hint="預設 183 天（半年）。到期只會列在下面的清單，不會自動刪除；交易內容與簽署紀錄一律保存五年。"
      />
    </SettingsForm>
  );
}
