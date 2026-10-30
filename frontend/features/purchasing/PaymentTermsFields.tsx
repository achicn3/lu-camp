"use client";
// 供應商付款條件的輸入（docs/99 §5.2，限管理者）：先付、貨到付款、月結（每月幾號付上個月的）、
// 到貨後幾天付。採購單送出時會把當下的條件記下來，之後改這裡不影響已經下的單。
import type { TermsChoice } from "@/features/purchasing/purchasing";

export function PaymentTermsFields({
  terms,
  value,
  onTerms,
  onValue,
  labelPrefix = "",
}: {
  terms: TermsChoice;
  value: string;
  onTerms: (terms: TermsChoice) => void;
  onValue: (value: string) => void;
  /** 同一頁有兩組（新增、編輯）時用來區分欄位名稱。 */
  labelPrefix?: string;
}) {
  return (
    <>
      <label className="field">
        <span>付款條件</span>
        <select
          aria-label={`${labelPrefix}付款條件`}
          value={terms}
          onChange={(e) => onTerms(e.target.value as TermsChoice)}
        >
          <option value="">未設定</option>
          <option value="PREPAID">先付（下單就付）</option>
          <option value="COD">貨到付款</option>
          <option value="MONTHLY">月結</option>
          <option value="NET_DAYS">到貨後幾天內付</option>
        </select>
      </label>
      {(terms === "MONTHLY" || terms === "NET_DAYS") && (
        <label className="field">
          <span>{terms === "MONTHLY" ? "每月幾號付上個月的" : "到貨後幾天內付"}</span>
          <input
            aria-label={`${labelPrefix}${terms === "MONTHLY" ? "每月幾號付" : "到貨後幾天"}`}
            inputMode="numeric"
            value={value}
            onChange={(e) => onValue(e.target.value)}
          />
        </label>
      )}
    </>
  );
}
