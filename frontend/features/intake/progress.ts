// 排隊收購的估價進度：報到時點清的件數 vs 已估的件數（只看件數，不看幾項）。
export interface EstimateProgress {
  /** 進度條百分比（0–100；估多了也封頂 100）。 */
  pct: number;
  /** 還差幾件沒估（估多了為 0）。 */
  missing: number;
  /** 比實收多估了幾件（沒多為 0）。 */
  over: number;
}

export function estimateProgress(declared: number, estimated: number): EstimateProgress {
  const pct = declared > 0 ? Math.min(100, Math.round((estimated / declared) * 100)) : 0;
  return {
    pct,
    missing: Math.max(0, declared - estimated),
    over: Math.max(0, estimated - declared),
  };
}
