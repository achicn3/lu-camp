"use client";
// /reports 報表頁（MANAGER 專用）。
// 10 個分頁：今日營運（dashboard）、趨勢、現金對帳、銷售毛利、庫存價值、寄售應付
//           + 購物金 4 分頁（負債/流量/效益指標/對帳）。
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import {
  EFFECTIVENESS_LABELS,
  FINANCIAL_GRANULARITY_OPTIONS,
  GRANULARITY_OPTIONS,
  computeChartScaling,
  defaultDateRange,
  exclusiveEnd,
  isoDate,
  startOfDay,
  triggerDownload,
} from "@/features/reports/reports";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";
import { getToken } from "@/lib/token";

import "./reports.css";

// -- Generated types --
type LiabilityReport = components["schemas"]["LiabilityReport"];
type FlowsReport = components["schemas"]["FlowsReport"];
type EffectivenessReport = components["schemas"]["EffectivenessReport"];
type ReconciliationReport = components["schemas"]["ReconciliationReport"];
type DailySummaryReport = components["schemas"]["DailySummaryReport"];
type TrendsReport = components["schemas"]["TrendsReport"];
type DailyCashReport = components["schemas"]["DailyCashReport"];
type SalesMarginReport = components["schemas"]["SalesMarginReport"];
type InventoryValueReport = components["schemas"]["InventoryValueReport"];
type ConsignmentPayablesReport = components["schemas"]["ConsignmentPayablesReport"];
type CampaignPerformanceReport = components["schemas"]["CampaignPerformanceReport"];

type Tab =
  | "dashboard"
  | "trends"
  | "daily-cash"
  | "sales-margin"
  | "campaign-performance"
  | "inventory-value"
  | "consignment-payables"
  | "liability"
  | "flows"
  | "effectiveness"
  | "reconciliation";

const TABS: { key: Tab; label: string }[] = [
  { key: "dashboard", label: "今日營運" },
  { key: "trends", label: "趨勢" },
  { key: "daily-cash", label: "現金對帳" },
  { key: "sales-margin", label: "銷售毛利" },
  { key: "campaign-performance", label: "活動成效" },
  { key: "inventory-value", label: "庫存價值" },
  { key: "consignment-payables", label: "寄售應付" },
  { key: "liability", label: "負債" },
  { key: "flows", label: "流量" },
  { key: "effectiveness", label: "效益指標" },
  { key: "reconciliation", label: "對帳" },
];

// -- Shared sub-components --

function MoneyText({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <span className="money">N/A</span>;
  const parsed = parseNtd(value);
  return <span className="money">{parsed === null ? value : formatNtd(parsed)}</span>;
}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function DownloadButtons({ onDownload }: { onDownload: (fmt: "csv" | "xlsx") => void }) {
  return (
    <div className="rpt-download-bar">
      <button type="button" className="btn-ghost" onClick={() => onDownload("csv")}>
        CSV
      </button>
      <button type="button" className="btn-ghost" onClick={() => onDownload("xlsx")}>
        Excel
      </button>
    </div>
  );
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <p role="alert" className="form-error">
      {message}
    </p>
  );
}

// -- Download helper --

async function downloadReport(url: string, filename: string): Promise<void> {
  const token = getToken();
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    window.alert(`下載失敗（${response.status}）`);
    return;
  }
  const blob = await response.blob();
  triggerDownload(blob, filename);
}

function buildExportUrl(basePath: string, format: "csv" | "xlsx", params?: Record<string, string>): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const url = new URL(`${baseUrl}${basePath}`);
  url.searchParams.set("format", format);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }
  }
  return url.toString();
}

// -- SVG Trend Chart Component (lightweight, zero-dependency) --

interface TrendChartProps {
  rows: { period: string; recognized_revenue: string; gross_margin: string }[];
}

function TrendChart({ rows }: TrendChartProps) {
  if (rows.length === 0) return <p className="hint">查無資料，無法繪圖</p>;

  const revenueValues = rows.map((r) => parseNtd(r.recognized_revenue) ?? 0);
  const marginValues = rows.map((r) => parseNtd(r.gross_margin) ?? 0);
  const allValues = [...revenueValues, ...marginValues];

  const { min: yMin, max: yMax, ticks } = computeChartScaling(allValues);

  const chartWidth = 600;
  const chartHeight = 300;
  const padding = { top: 20, right: 20, bottom: 60, left: 80 };
  const innerWidth = chartWidth - padding.left - padding.right;
  const innerHeight = chartHeight - padding.top - padding.bottom;

  const yRange = yMax - yMin || 1;
  const toY = (v: number) => padding.top + innerHeight - ((v - yMin) / yRange) * innerHeight;
  const toX = (i: number) =>
    padding.left + (rows.length === 1 ? innerWidth / 2 : (i / (rows.length - 1)) * innerWidth);

  const revenuePath = rows
    .map((_, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(revenueValues[i]).toFixed(1)}`)
    .join(" ");
  const marginPath = rows
    .map((_, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(marginValues[i]).toFixed(1)}`)
    .join(" ");

  return (
    <div className="rpt-chart-wrap">
      <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="rpt-trend-chart" role="img" aria-label="趨勢圖">
        {/* Y-axis grid lines and labels */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={padding.left}
              x2={chartWidth - padding.right}
              y1={toY(tick)}
              y2={toY(tick)}
              stroke="var(--border)"
              strokeDasharray="4 2"
            />
            <text x={padding.left - 8} y={toY(tick) + 4} textAnchor="end" fontSize="11" fill="var(--ink-soft)">
              {formatNtd(tick)}
            </text>
          </g>
        ))}

        {/* X-axis labels */}
        {rows.map((row, i) => (
          <text
            key={row.period}
            x={toX(i)}
            y={chartHeight - padding.bottom + 18}
            textAnchor="middle"
            fontSize="10"
            fill="var(--ink-soft)"
            transform={rows.length > 7 ? `rotate(-45, ${toX(i)}, ${chartHeight - padding.bottom + 18})` : undefined}
          >
            {row.period}
          </text>
        ))}

        {/* Revenue line */}
        <path d={revenuePath} fill="none" stroke="var(--accent)" strokeWidth="2.5" />
        {rows.map((_, i) => (
          <circle key={`rev-${revenueValues[i]}-${i}`} cx={toX(i)} cy={toY(revenueValues[i])} r="3.5" fill="var(--accent)" />
        ))}

        {/* Margin line */}
        <path d={marginPath} fill="none" stroke="var(--info)" strokeWidth="2.5" />
        {rows.map((_, i) => (
          <circle key={`mar-${marginValues[i]}-${i}`} cx={toX(i)} cy={toY(marginValues[i])} r="3.5" fill="var(--info)" />
        ))}

        {/* Legend */}
        <rect x={chartWidth - padding.right - 150} y={padding.top} width="12" height="12" fill="var(--accent)" rx="2" />
        <text x={chartWidth - padding.right - 134} y={padding.top + 11} fontSize="12" fill="var(--ink)">
          認列營收
        </text>
        <rect x={chartWidth - padding.right - 70} y={padding.top} width="12" height="12" fill="var(--info)" rx="2" />
        <text x={chartWidth - padding.right - 54} y={padding.top + 11} fontSize="12" fill="var(--ink)">
          毛利
        </text>
      </svg>
    </div>
  );
}

// ============================================================
// Phase 6 Financial Report Panels
// ============================================================

// -- Dashboard Panel (Today's Operations) --

function DashboardPanel() {
  const [date, setDate] = useState(isoDate(new Date()));

  const query = useQuery({
    queryKey: ["reports", "daily-summary", date],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/daily-summary", {
        params: { query: { date } },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取每日營運報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: DailySummaryReport = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/daily-summary", fmt, { date });
    void downloadReport(url, `daily-summary-${date}.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          日期
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>

      <dl className="rpt-summary rpt-dashboard-cards">
        <div className="rpt-stat rpt-stat-hero">
          <dt>營業額</dt>
          <dd><MoneyText value={report.gross_turnover} /></dd>
        </div>
        <div className="rpt-stat rpt-stat-hero">
          <dt>認列營收</dt>
          <dd><MoneyText value={report.recognized_revenue} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>二手營收</dt>
          <dd><MoneyText value={report.secondhand_revenue} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>餐飲營收</dt>
          <dd><MoneyText value={report.food_revenue} /></dd>
        </div>
        <div className="rpt-stat rpt-stat-hero">
          <dt>毛利</dt>
          <dd>
            <MoneyText value={report.gross_margin} />
            {report.gross_margin_rate && (
              <span className="rpt-rate"> ({report.gross_margin_rate})</span>
            )}
          </dd>
        </div>
        <div className="rpt-stat">
          <dt>當日現金支出</dt>
          <dd><MoneyText value={report.total_cash_out} /></dd>
        </div>
        <div className="rpt-stat rpt-stat-estimate">
          <dt>
            估算淨利
            <span className="rpt-badge-estimate">估計值</span>
          </dt>
          <dd><MoneyText value={report.estimated_net_income} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>客單價</dt>
          <dd><MoneyText value={report.avg_ticket} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>交易筆數</dt>
          <dd className="money">{report.transaction_count}</dd>
        </div>
        <div className="rpt-stat">
          <dt>購物金發出</dt>
          <dd><MoneyText value={report.store_credit_issued} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>購物金兌付</dt>
          <dd><MoneyText value={report.store_credit_redeemed} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>現金差異</dt>
          <dd><MoneyText value={report.cash_variance} /></dd>
        </div>
      </dl>

      {report.estimated_net_income_note && (
        <p className="rpt-dashboard-footnote">
          <span className="rpt-badge-estimate">估計值</span>
          估算淨利說明：{report.estimated_net_income_note}
        </p>
      )}

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Trends Panel --

function TrendsPanel() {
  const defaults = defaultDateRange();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);
  const [granularity, setGranularity] = useState<"day" | "week" | "month" | "quarter">("day");

  const query = useQuery({
    queryKey: ["reports", "trends", { from, to, granularity }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/trends", {
        params: {
          query: {
            from: startOfDay(from),
            to: exclusiveEnd(to),
            granularity,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取趨勢報表失敗");
    },
  });

  const report: TrendsReport | undefined = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/trends", fmt, {
      from: startOfDay(from),
      to: exclusiveEnd(to),
      granularity,
    });
    void downloadReport(url, `trends-${from}-${to}.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          起始日期
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          結束日期
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label>
          粒度
          <select
            value={granularity}
            onChange={(e) => setGranularity(e.target.value as "day" | "week" | "month" | "quarter")}
          >
            {FINANCIAL_GRANULARITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
      </div>

      {query.isPending && <p className="hint">載入中...</p>}
      {query.isError && <ErrorBlock message={query.error.message} />}
      {report && (
        <>
          <TrendChart rows={report.rows} />

          <div className="inv-table-wrap">
            <table className="inv-table">
              <thead>
                <tr>
                  <th>期間</th>
                  <th>認列營收</th>
                  <th>毛利</th>
                  <th>毛利率</th>
                  <th>營業額</th>
                  <th>交易數</th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td><MoneyText value={row.recognized_revenue} /></td>
                    <td><MoneyText value={row.gross_margin} /></td>
                    <td>{row.gross_margin_rate ?? "N/A"}</td>
                    <td><MoneyText value={row.gross_turnover} /></td>
                    <td className="money">{row.transaction_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.rows.length === 0 && <p className="hint">查無資料</p>}
          </div>

          <DownloadButtons onDownload={handleDownload} />
        </>
      )}
    </div>
  );
}

// -- Daily Cash Panel --

function DailyCashPanel() {
  const [date, setDate] = useState(isoDate(new Date()));

  const query = useQuery({
    queryKey: ["reports", "daily-cash", date],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/daily-cash", {
        params: { query: { date } },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取現金對帳報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: DailyCashReport = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/daily-cash", fmt, { date });
    void downloadReport(url, `daily-cash-${date}.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          日期
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>

      <h3>各 Session</h3>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>狀態</th>
              <th>開帳時間</th>
              <th>零用金</th>
              <th>現金銷售</th>
              <th>買斷支出</th>
              <th>寄售付款</th>
              <th>退貨退現</th>
              <th>作廢收回</th>
              <th>手動調整</th>
              <th>應有現金</th>
              <th>實點現金</th>
              <th>差異</th>
            </tr>
          </thead>
          <tbody>
            {report.sessions.map((s) => (
              <tr key={s.session_id}>
                <td className="money">{s.session_id}</td>
                <td>{s.status}</td>
                <td>{s.opened_at}</td>
                <td><MoneyText value={s.opening_float} /></td>
                <td><MoneyText value={s.cash_sales} /></td>
                <td><MoneyText value={s.buyout_out} /></td>
                <td><MoneyText value={s.consignment_payout_out} /></td>
                <td><MoneyText value={s.sale_refund_out} /></td>
                <td><MoneyText value={s.acquisition_void_in} /></td>
                <td><MoneyText value={s.manual_adjust_total} /></td>
                <td><MoneyText value={s.expected_amount} /></td>
                <td><MoneyText value={s.counted_amount} /></td>
                <td><MoneyText value={s.variance} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {report.sessions.length === 0 && <p className="hint">當日無 session</p>}
      </div>

      <h3>當日合計</h3>
      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>零用金</dt>
          <dd><MoneyText value={report.total_opening_float} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>現金銷售</dt>
          <dd><MoneyText value={report.total_cash_sales} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>買斷支出</dt>
          <dd><MoneyText value={report.total_buyout_out} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>寄售付款</dt>
          <dd><MoneyText value={report.total_consignment_payout_out} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>退貨退現</dt>
          <dd><MoneyText value={report.total_sale_refund_out} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>作廢收回</dt>
          <dd><MoneyText value={report.total_acquisition_void_in} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>手動調整</dt>
          <dd><MoneyText value={report.total_manual_adjust} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>應有現金</dt>
          <dd><MoneyText value={report.total_expected} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>實點現金</dt>
          <dd><MoneyText value={report.total_counted} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>差異</dt>
          <dd><MoneyText value={report.total_variance} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>購物金兌付（參考）</dt>
          <dd><MoneyText value={report.total_store_credit_redeemed_display_only} /></dd>
        </div>
      </dl>

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Sales Margin Panel --

function SalesMarginPanel() {
  const defaults = defaultDateRange();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);

  const query = useQuery({
    queryKey: ["reports", "sales-margin", { from, to }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/sales-margin", {
        params: {
          query: {
            from: startOfDay(from),
            to: exclusiveEnd(to),
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取銷售毛利報表失敗");
    },
  });

  const report: SalesMarginReport | undefined = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/sales-margin", fmt, {
      from: startOfDay(from),
      to: exclusiveEnd(to),
    });
    void downloadReport(url, `sales-margin-${from}-${to}.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          起始日期
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          結束日期
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>

      {query.isPending && <p className="hint">載入中...</p>}
      {query.isError && <ErrorBlock message={query.error.message} />}
      {report && (
        <>
          <dl className="rpt-summary">
            <div className="rpt-stat rpt-stat-hero">
              <dt>營業額</dt>
              <dd><MoneyText value={report.gross_turnover} /></dd>
            </div>
            <div className="rpt-stat rpt-stat-hero">
              <dt>認列營收</dt>
              <dd><MoneyText value={report.recognized_revenue} /></dd>
            </div>
            <div className="rpt-stat rpt-stat-hero">
              <dt>毛利</dt>
              <dd>
                <MoneyText value={report.gross_margin} />
                {report.gross_margin_rate && (
                  <span className="rpt-rate"> ({report.gross_margin_rate})</span>
                )}
              </dd>
            </div>
          </dl>

          <div className="inv-table-wrap">
            <table className="inv-table">
              <thead>
                <tr>
                  <th>指標</th>
                  <th>金額</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>買斷成本 (COGS)</td>
                  <td><MoneyText value={report.owned_cogs} /></td>
                </tr>
                <tr>
                  <td>散裝成本</td>
                  <td><MoneyText value={report.bulk_cogs} /></td>
                </tr>
                <tr>
                  <td>寄售抽成收入</td>
                  <td><MoneyText value={report.consignment_commission_income} /></td>
                </tr>
                <tr>
                  <td>二手營收</td>
                  <td><MoneyText value={report.secondhand_revenue} /></td>
                </tr>
                <tr>
                  <td>餐飲營收</td>
                  <td><MoneyText value={report.food_revenue} /></td>
                </tr>
                <tr>
                  <td>成本不明銷售額</td>
                  <td><MoneyText value={report.unknown_cost_sales} /></td>
                </tr>
                <tr>
                  <td>現金收入</td>
                  <td><MoneyText value={report.cash_received} /></td>
                </tr>
                <tr>
                  <td>購物金兌付</td>
                  <td><MoneyText value={report.store_credit_redeemed} /></td>
                </tr>
                <tr>
                  <td>交易筆數</td>
                  <td className="money">{report.transaction_count}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <DownloadButtons onDownload={handleDownload} />
        </>
      )}
    </div>
  );
}

// -- Inventory Value Panel --

function InventoryValuePanel() {
  const query = useQuery({
    queryKey: ["reports", "inventory-value"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/inventory-value");
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取庫存價值報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: InventoryValueReport = query.data;
  const aging = report.owned_cost_aging;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/inventory-value", fmt);
    void downloadReport(url, `inventory-value.${fmt}`);
  }

  return (
    <div>
      <h3>自有庫存</h3>
      <dl className="rpt-summary">
        <div className="rpt-stat rpt-stat-hero">
          <dt>總成本</dt>
          <dd><MoneyText value={report.total_owned_cost_value} /></dd>
        </div>
        <div className="rpt-stat rpt-stat-hero">
          <dt>總售價</dt>
          <dd><MoneyText value={report.total_owned_retail_value} /></dd>
        </div>
      </dl>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>類型</th>
              <th>數量</th>
              <th>成本</th>
              <th>售價</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>序號品</td>
              <td className="money">{report.owned_serialized_count}</td>
              <td><MoneyText value={report.owned_serialized_cost} /></td>
              <td><MoneyText value={report.owned_serialized_retail} /></td>
            </tr>
            <tr>
              <td>散裝批</td>
              <td className="money">{report.owned_bulk_remaining_qty}</td>
              <td><MoneyText value={report.owned_bulk_cost} /></td>
              <td><MoneyText value={report.owned_bulk_retail} /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>庫齡（自有成本價值）</h3>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>&lt;30 天</th>
              <th>30-90 天</th>
              <th>90-180 天</th>
              <th>180-365 天</th>
              <th>&gt;365 天</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><MoneyText value={aging.lt_30d} /></td>
              <td><MoneyText value={aging.d30_90} /></td>
              <td><MoneyText value={aging.d90_180} /></td>
              <td><MoneyText value={aging.d180_365} /></td>
              <td><MoneyText value={aging.gt_365d} /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>寄售在庫</h3>
      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>序號品</dt>
          <dd className="money">{report.consignment_serialized_count} 件</dd>
        </div>
        <div className="rpt-stat">
          <dt>散裝剩餘</dt>
          <dd className="money">{report.consignment_bulk_remaining_qty} 件</dd>
        </div>
        <div className="rpt-stat">
          <dt>售價總額</dt>
          <dd><MoneyText value={report.consignment_inventory_gross} /></dd>
        </div>
      </dl>

      <h3>數量型商品</h3>
      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>數量</dt>
          <dd className="money">{report.catalog_total_qty} 件</dd>
        </div>
        <div className="rpt-stat">
          <dt>售價</dt>
          <dd><MoneyText value={report.catalog_retail_value} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>成本</dt>
          <dd><MoneyText value={report.catalog_cost_value} /></dd>
        </div>
      </dl>

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Consignment Payables Panel --

function ConsignmentPayablesPanel() {
  const [status, setStatus] = useState<"PENDING" | "PAID" | "CANCELLED" | "ALL">("ALL");

  const query = useQuery({
    queryKey: ["reports", "consignment-payables", status],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/consignment-payables", {
        params: { query: { status } },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取寄售應付報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: ConsignmentPayablesReport = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/consignment-payables", fmt, { status });
    void downloadReport(url, `consignment-payables-${status}.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          狀態篩選
          <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="ALL">ALL</option>
            <option value="PENDING">PENDING</option>
            <option value="PAID">PAID</option>
            <option value="CANCELLED">CANCELLED</option>
          </select>
        </label>
      </div>

      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>待付</dt>
          <dd><MoneyText value={report.total_pending_payout} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>已付</dt>
          <dd><MoneyText value={report.total_paid_payout} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>取消</dt>
          <dd><MoneyText value={report.total_cancelled_payout} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>需追回</dt>
          <dd><MoneyText value={report.total_reclaim_needed_payout} /></dd>
        </div>
      </dl>

      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>結算 ID</th>
              <th>商品</th>
              <th>寄售人</th>
              <th>電話</th>
              <th>售價</th>
              <th>抽成</th>
              <th>應付</th>
              <th>狀態</th>
              <th>需追回</th>
              <th>銷售時間</th>
            </tr>
          </thead>
          <tbody>
            {report.rows.map((row) => (
              <tr key={row.settlement_id}>
                <td className="money">{row.settlement_id}</td>
                <td>{row.item_name}</td>
                <td>{row.consignor_name ?? "-"}</td>
                <td>{row.consignor_phone ?? "-"}</td>
                <td><MoneyText value={row.gross} /></td>
                <td><MoneyText value={row.commission_amount} /></td>
                <td><MoneyText value={row.payout_amount} /></td>
                <td>{row.status}</td>
                <td>{row.reclaim_needed ? "是" : "否"}</td>
                <td>{row.sale_created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {report.rows.length === 0 && <p className="hint">查無資料</p>}
      </div>

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Campaign Performance Panel (C4，docs/21) --

function CampaignPerformancePanel() {
  const query = useQuery({
    queryKey: ["reports", "campaign-performance"],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/v1/reports/campaign-performance",
      );
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取活動成效報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: CampaignPerformanceReport = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/campaign-performance", fmt);
    void downloadReport(url, `campaign-performance.${fmt}`);
  }

  const fmtDate = (iso: string) => new Date(iso).toLocaleDateString("zh-TW");

  return (
    <div>
      <p className="hint">
        每檔生效中／已結束活動的成效：成效指標取活動期間（與「銷售毛利」同源），活動折讓總額為該活動實際發出的折讓。
      </p>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>活動</th>
              <th>狀態</th>
              <th>折扣</th>
              <th>期間</th>
              <th>活動折讓</th>
              <th>營業額</th>
              <th>認列營收</th>
              <th>毛利</th>
              <th>毛利率</th>
              <th>筆數</th>
            </tr>
          </thead>
          <tbody>
            {report.rows.map((row) => (
              <tr key={row.campaign_id}>
                <td>{row.name}</td>
                <td>{row.status}</td>
                <td>{row.discount_pct}%</td>
                <td>
                  {fmtDate(row.starts_at)} ~ {fmtDate(row.ends_at)}
                </td>
                <td>
                  <MoneyText value={row.campaign_discount_total} />
                </td>
                <td>
                  <MoneyText value={row.gross_turnover} />
                </td>
                <td>
                  <MoneyText value={row.recognized_revenue} />
                </td>
                <td>
                  <MoneyText value={row.gross_margin} />
                </td>
                <td>
                  {row.gross_margin_rate === null ? "N/A" : row.gross_margin_rate}
                </td>
                <td className="money">{row.transaction_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {report.rows.length === 0 && <p className="hint">尚無生效中／已結束的活動</p>}
      </div>

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// ============================================================
// Store Credit Report Panels (existing)
// ============================================================

// -- Liability Panel --

function LiabilityPanel() {
  const query = useQuery({
    queryKey: ["reports", "sc-liability"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/store-credit/liability");
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取負債報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: LiabilityReport = query.data;
  const buckets = report.aging_buckets;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/store-credit/liability", fmt);
    void downloadReport(url, `store-credit-liability.${fmt}`);
  }

  return (
    <div>
      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>未兌付總負債</dt>
          <dd><MoneyText value={report.total_outstanding} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>負債健康比</dt>
          <dd>{report.liability_health_ratio ?? "N/A"}</dd>
        </div>
      </dl>

      <h3>帳齡分桶</h3>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>&lt;30 天</th>
              <th>30-90 天</th>
              <th>90-180 天</th>
              <th>180-365 天</th>
              <th>&gt;365 天</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><MoneyText value={buckets.lt_30d} /></td>
              <td><MoneyText value={buckets.d30_90} /></td>
              <td><MoneyText value={buckets.d90_180} /></td>
              <td><MoneyText value={buckets.d180_365} /></td>
              <td><MoneyText value={buckets.gt_365d} /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>各會員餘額</h3>
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>會員</th>
              <th>餘額</th>
            </tr>
          </thead>
          <tbody>
            {report.per_member.map((m) => (
              <tr key={m.contact_id}>
                <td>{m.name}</td>
                <td><MoneyText value={m.balance} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {report.per_member.length === 0 && <p className="hint">無會員持有購物金</p>}
      </div>

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Flows Panel --

function FlowsPanel() {
  const defaults = defaultDateRange();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);
  const [granularity, setGranularity] = useState<"day" | "week" | "month">("day");

  const query = useQuery({
    queryKey: ["reports", "sc-flows", { from, to, granularity }],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/reports/store-credit/flows", {
        params: {
          query: {
            from: startOfDay(from),
            to: exclusiveEnd(to),
            granularity,
          },
        },
      });
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取流量報表失敗");
    },
  });

  const report: FlowsReport | undefined = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/store-credit/flows", fmt, {
      from: startOfDay(from),
      to: exclusiveEnd(to),
      granularity,
    });
    void downloadReport(url, `store-credit-flows.${fmt}`);
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          起始日期
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          結束日期
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label>
          粒度
          <select
            value={granularity}
            onChange={(e) => setGranularity(e.target.value as "day" | "week" | "month")}
          >
            {GRANULARITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
      </div>

      {query.isPending && <p className="hint">載入中...</p>}
      {query.isError && <ErrorBlock message={query.error.message} />}
      {report && (
        <>
          <div className="inv-table-wrap">
            <table className="inv-table">
              <thead>
                <tr>
                  <th>期間</th>
                  <th>發出</th>
                  <th>兌付</th>
                  <th>淨變化</th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td><MoneyText value={row.issued} /></td>
                    <td><MoneyText value={row.redeemed} /></td>
                    <td><MoneyText value={row.net_change} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.rows.length === 0 && <p className="hint">查無資料</p>}
          </div>
          <DownloadButtons onDownload={handleDownload} />
        </>
      )}
    </div>
  );
}

// -- Effectiveness Panel --

const METRIC_KEYS: (keyof typeof EFFECTIVENESS_LABELS)[] = [
  "take_rate",
  "avg_premium_rate",
  "beta_retention",
  "excess_spend_rate",
  "alpha_incremental",
  "gross_margin_m",
  "delta_per_1000",
];

function EffectivenessPanel() {
  const defaults = defaultDateRange();
  const [from, setFrom] = useState(defaults.from);
  const [to, setTo] = useState(defaults.to);

  const query = useQuery({
    queryKey: ["reports", "sc-effectiveness", { from, to }],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/v1/reports/store-credit/effectiveness",
        {
          params: {
            query: {
              from: startOfDay(from),
              to: exclusiveEnd(to),
            },
          },
        },
      );
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取效益指標失敗");
    },
  });

  const report: EffectivenessReport | undefined = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/store-credit/effectiveness", fmt, {
      from: startOfDay(from),
      to: exclusiveEnd(to),
    });
    void downloadReport(url, `store-credit-effectiveness.${fmt}`);
  }

  function metricValue(key: string): string | null {
    if (!report) return null;
    return (report as unknown as Record<string, string | null>)[key] ?? null;
  }

  return (
    <div>
      <div className="rpt-filters">
        <label>
          起始日期
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          結束日期
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>

      {query.isPending && <p className="hint">載入中...</p>}
      {query.isError && <ErrorBlock message={query.error.message} />}
      {report && (
        <>
          <div className="inv-table-wrap">
            <table className="inv-table">
              <thead>
                <tr>
                  <th>指標</th>
                  <th>值</th>
                  <th>備註</th>
                </tr>
              </thead>
              <tbody>
                {METRIC_KEYS.map((key) => {
                  const isEstimate = report.estimate_fields.includes(key);
                  const isAlpha = key === "alpha_incremental";
                  const val = metricValue(key);
                  return (
                    <tr key={key}>
                      <td>
                        {EFFECTIVENESS_LABELS[key]}
                        {isEstimate && <span className="rpt-badge-estimate">估計值</span>}
                        {isAlpha && <span className="rpt-badge-proxy">代理法</span>}
                      </td>
                      <td>{val ?? "N/A"}</td>
                      <td />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {report.alpha_sample_insufficient && (
            <p className="rpt-note">樣本不足</p>
          )}

          <p className="hint">{report.alpha_method_note}</p>

          <DownloadButtons onDownload={handleDownload} />
        </>
      )}
    </div>
  );
}

// -- Reconciliation Panel --

function ReconciliationPanel() {
  const query = useQuery({
    queryKey: ["reports", "sc-reconciliation"],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/v1/reports/store-credit/reconciliation",
      );
      if (response.ok && data) return data;
      throw new Error(extractDetail(error) ?? "讀取對帳報表失敗");
    },
  });

  if (query.isPending) return <p className="hint">載入中...</p>;
  if (query.isError) return <ErrorBlock message={query.error.message} />;

  const report: ReconciliationReport = query.data;

  function handleDownload(fmt: "csv" | "xlsx") {
    const url = buildExportUrl("/api/v1/reports/store-credit/reconciliation", fmt);
    void downloadReport(url, `store-credit-reconciliation.${fmt}`);
  }

  return (
    <div>
      <dl className="rpt-summary">
        <div className="rpt-stat">
          <dt>帳本總負債</dt>
          <dd><MoneyText value={report.ledger_total_outstanding} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>快取總負債</dt>
          <dd><MoneyText value={report.cached_total_outstanding} /></dd>
        </div>
        <div className="rpt-stat">
          <dt>快取可信</dt>
          <dd className={report.cached_total_trustworthy ? "rpt-ok" : "rpt-mismatch"}>
            {report.cached_total_trustworthy ? "是" : "否"}
          </dd>
        </div>
      </dl>

      {report.mismatches.length > 0 && (
        <>
          <h3>不一致帳戶</h3>
          <div className="inv-table-wrap">
            <table className="inv-table">
              <thead>
                <tr>
                  <th>帳戶</th>
                  <th>詳情</th>
                </tr>
              </thead>
              <tbody>
                {report.mismatches.map((m, i) => (
                  <tr key={i}>
                    <td>{JSON.stringify(m)}</td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {report.mismatches.length === 0 && (
        <p className="hint rpt-ok">所有帳戶一致，無異常。</p>
      )}

      <DownloadButtons onDownload={handleDownload} />
    </div>
  );
}

// -- Tab Content --

function TabContent({ tab }: { tab: Tab }): ReactNode {
  switch (tab) {
    case "dashboard":
      return <DashboardPanel />;
    case "trends":
      return <TrendsPanel />;
    case "daily-cash":
      return <DailyCashPanel />;
    case "sales-margin":
      return <SalesMarginPanel />;
    case "inventory-value":
      return <InventoryValuePanel />;
    case "consignment-payables":
      return <ConsignmentPayablesPanel />;
    case "campaign-performance":
      return <CampaignPerformancePanel />;
    case "liability":
      return <LiabilityPanel />;
    case "flows":
      return <FlowsPanel />;
    case "effectiveness":
      return <EffectivenessPanel />;
    case "reconciliation":
      return <ReconciliationPanel />;
  }
}

// -- Main Page --

export default function ReportsPage() {
  const [tab, setTab] = useState<Tab>("dashboard");
  // 不以 token 的 role 把關：永不過期 token 的 role claim 可能過時（升/降權後未重新登入）。
  // 改以後端授權為準——探測一個 MANAGER-only 端點，依其 401/403 決定是否顯示「需管理者權限」。
  const access = useQuery({
    queryKey: ["reports", "access"],
    queryFn: async () => {
      const { response } = await api.GET("/api/v1/reports/store-credit/liability");
      if (response.status === 401 || response.status === 403) return "denied" as const;
      if (!response.ok) throw new Error(`報表服務暫時無法使用（${response.status}）`);
      return "granted" as const;
    },
    retry: false,
  });

  if (access.isFetching) {
    return (
      <section>
        <h1 className="page-title">報表</h1>
        <p className="hint">載入中...</p>
      </section>
    );
  }
  if (access.isError) {
    return (
      <section>
        <h1 className="page-title">報表</h1>
        <ErrorBlock message="無法連線報表服務，請稍後再試" />
      </section>
    );
  }
  if (access.data === "denied") {
    return (
      <section>
        <h1 className="page-title">報表</h1>
        <p>需管理者權限</p>
      </section>
    );
  }

  return (
    <section>
      <h1 className="page-title">報表</h1>

      <div className="inv-tabs rpt-tabs-wrap" role="tablist">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "inv-tab inv-tab-active" : "inv-tab"}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <TabContent tab={tab} />
    </section>
  );
}
