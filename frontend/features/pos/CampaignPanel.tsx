"use client";
// POS「本筆套用的活動」（docs/40 P1c）：列出這一筆實際套到的門市活動與各自折了多少；
// 店員可以按「這筆不套用」（不需主管核准、原因可不填），取消後即時重算，並可「恢復套用」。
// 金額一律來自後端試算，這裡只負責顯示與收集店員的選擇。
import { useState } from "react";

import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Applied = components["schemas"]["SaleQuoteCampaignRead"];
type Disabled = components["schemas"]["SaleDisabledCampaignRead"];

export function CampaignPanel({
  applied,
  disabled,
  locked,
  onDisable,
  onRestore,
}: {
  applied: Applied[];
  disabled: Disabled[];
  /** 購物車鎖住（簽署中、付款處理中）時不能改。 */
  locked: boolean;
  onDisable: (campaignId: number, reason: string | null) => void;
  onRestore: (campaignId: number) => void;
}) {
  const [asking, setAsking] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  if (applied.length === 0 && disabled.length === 0) return null;

  function confirm(campaignId: number) {
    onDisable(campaignId, reason.trim() || null);
    setAsking(null);
    setReason("");
  }

  return (
    <section className="pos-campaigns" aria-label="本筆套用的活動">
      <h3 className="pos-campaigns-title">本筆套用的活動</h3>
      <ul className="pos-campaigns-list">
        {applied.map((c) => (
          <li key={c.campaign_id}>
            <div className="pos-campaigns-row">
              <span>{c.name}</span>
              <span>
                −<span className="money">${formatNtd(parseNtd(c.discount_amount) ?? 0)}</span>
              </span>
              <button
                type="button"
                className="btn-ghost"
                disabled={locked}
                onClick={() => {
                  setAsking(c.campaign_id);
                  setReason("");
                }}
              >
                這筆不套用
              </button>
            </div>
            {asking === c.campaign_id && (
              <div className="pos-campaigns-ask">
                <label className="field">
                  <span className="field-label">原因（可不填）</span>
                  <input
                    aria-label="不套用原因"
                    value={reason}
                    maxLength={200}
                    onChange={(e) => setReason(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        confirm(c.campaign_id);
                      }
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => confirm(c.campaign_id)}
                >
                  確定不套用
                </button>
                <button type="button" className="btn-ghost" onClick={() => setAsking(null)}>
                  取消
                </button>
              </div>
            )}
          </li>
        ))}
        {disabled.map((c) => (
          <li key={c.campaign_id} className="pos-campaigns-off">
            <div className="pos-campaigns-row">
              <span>
                {c.name}
                <span className="row-sub">這筆不套用</span>
              </span>
              <button
                type="button"
                className="btn-ghost"
                disabled={locked}
                onClick={() => onRestore(c.campaign_id)}
              >
                恢復套用
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
