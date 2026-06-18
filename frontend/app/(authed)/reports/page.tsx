"use client";
// /reports 報表頁（MANAGER 專用）：購物金報表區（docs/10 §/reports、docs/16 §5）。
// 四個分頁：負債（liability）、流量（flows）、效益指標（effectiveness）、對帳（reconciliation）。
// 其他報表（現金對帳/營收成本/庫存/寄售/趨勢）待後端端點完成後補齊。
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import {
  EFFECTIVENESS_LABELS,
  GRANULARITY_OPTIONS,
  defaultDateRange,
  exclusiveEnd,
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

type Tab = "liability" | "flows" | "effectiveness" | "reconciliation";

const TABS: { key: Tab; label: string }[] = [
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

// 匯出走原生 fetch（非 api client）下載二進位檔，故必須自行帶上 Bearer——
// 報表匯出端點為 MANAGER 限定，缺 token 會 401 且靜默無檔。
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
  const [tab, setTab] = useState<Tab>("liability");
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
    retry: false, // gate 探測：權限/錯誤即時決斷，不重試
  });

  if (access.isPending) {
    return (
      <section>
        <h1 className="page-title">報表</h1>
        <p className="hint">載入中...</p>
      </section>
    );
  }
  // 連線/伺服器錯誤（探測 throw）與「無權限」分流：不可把斷線誤報為權限不足。
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

      <div className="inv-tabs" role="tablist">
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

      <div className="rpt-other-note">
        （其他報表待後端端點：每日現金對帳、營收/成本/毛利、庫存價值/庫齡、寄售應付、趨勢。）
      </div>
    </section>
  );
}
