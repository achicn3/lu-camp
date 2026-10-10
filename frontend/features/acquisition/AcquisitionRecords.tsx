"use client";
// 收購紀錄清單（2026-09-23 裁示）：新到舊、可篩選與翻頁；店員也能看，作廢鈕限管理者。
// 能不能作廢由後端 void_block 事先算好（口徑與作廢端點一致）——不能作廢的單按鈕反灰並講原因，
// 店長不必按下去才被拒絕。每列只有一顆「作廢」（2026-10-02 裁示）：買斷單跳出商品勾選視窗
// （預設全勾＝整張），散裝單直接整張；最後都經 VoidConfirmDialog 填原因、二次確認（後端是最終權威）。
// 全額購物金撥款的單另有「改成付現」（客人反悔要現金；2026-10-10）。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { canConvertToCash, ConvertToCashAction } from "@/features/acquisition/ConvertToCashAction";
import { ACQ_TYPE_LABEL } from "@/features/acquisition/labels";
import {
  errorDetail,
  recordVoidHint,
  recordVoidMode,
  type RecordVoidMode,
} from "@/features/acquisition/void";
import { SelectVoidDialog } from "@/features/acquisition/SelectVoidDialog";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";
import { Pagination } from "@/features/common/Pagination";
import { exclusiveEnd, startOfDay } from "@/features/reports/reports";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { decodeSession } from "@/lib/auth";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Row = components["schemas"]["AcquisitionListItem"];
type AcquisitionType = components["schemas"]["AcquisitionType"];
type VoidResult = components["schemas"]["AcquisitionVoidResult"];

const PAGE_SIZE = 50;

const TYPE_FILTERS: { key: AcquisitionType | null; label: string }[] = [
  { key: null, label: "全部類型" },
  { key: "BUYOUT", label: ACQ_TYPE_LABEL.BUYOUT },
  { key: "CONSIGNMENT", label: ACQ_TYPE_LABEL.CONSIGNMENT },
  { key: "BULK_LOT", label: ACQ_TYPE_LABEL.BULK_LOT },
];

// voided：null＝全部、false＝只看有效、true＝只看已作廢。
const STATUS_FILTERS: { key: boolean | null; label: string }[] = [
  { key: null, label: "全部狀態" },
  { key: false, label: "只看有效" },
  { key: true, label: "只看已作廢" },
];

function ntd(value: string | null | undefined): string {
  const parsed = value == null ? null : parseNtd(value);
  return parsed === null ? "0" : formatNtd(parsed);
}

/** 「營燈、睡袋、爐頭 等 4 件」——列出的品名比件數少時才補「等 N 件」。 */
function itemsText(row: Row): string {
  if (row.item_count === 0) return "—";
  const names = row.item_names.join("、");
  return row.item_count > row.item_names.length ? `${names} 等 ${row.item_count} 件` : names;
}

/** 付了什麼：現金、購物金或兩者；寄售當下不付錢。 */
function payoutText(row: Row): string {
  if (row.type === "CONSIGNMENT") return "賣出後結算";
  const parts: string[] = [];
  if (row.payout_cash_amount != null && parseNtd(row.payout_cash_amount) !== 0) {
    parts.push(`現金 ${ntd(row.payout_cash_amount)}`);
  }
  if (
    row.payout_credit_cash_equivalent != null &&
    parseNtd(row.payout_credit_cash_equivalent) !== 0
  ) {
    parts.push(`購物金 ${ntd(row.payout_credit_cash_equivalent)}`);
  }
  return parts.length > 0 ? parts.join("＋") : "—";
}

/** 每列一顆「作廢」：依 recordVoidMode 決定能不能按、按下去開哪種視窗，旁邊講清楚原因。 */
function RecordVoidCell({
  row,
  onVoid,
}: {
  row: Row;
  onVoid: (mode: Exclude<RecordVoidMode, null>) => void;
}) {
  const mode = recordVoidMode(row);
  const hint = recordVoidHint(row);
  return (
    <div className="acq-records-void">
      <button
        type="button"
        className="btn-danger"
        disabled={mode === null}
        onClick={() => {
          if (mode !== null) onVoid(mode);
        }}
      >
        作廢
      </button>
      {hint !== null && <span className="row-sub">{hint}</span>}
    </div>
  );
}

export function AcquisitionRecords() {
  const queryClient = useQueryClient();
  const isManager = decodeSession()?.role === "MANAGER";
  const [page, setPage] = useState(0);
  const [type, setType] = useState<AcquisitionType | null>(null);
  const [voided, setVoided] = useState<boolean | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  // 賣方搜尋（提交式）：輸入框與已提交值分開，避免每次按鍵都打 API。
  const [search, setSearch] = useState("");
  const [submittedSearch, setSubmittedSearch] = useState("");
  // 買斷單作廢的商品勾選視窗：哪一張、要不要預設全勾（見 recordVoidMode）。
  const [selecting, setSelecting] = useState<{ id: number; preselectAll: boolean } | null>(null);
  // 穩定的參照：對話視窗的 Esc 監聽以它為依賴，每次重繪都換新函式會反覆掛卸監聽。
  const closeSelecting = useCallback(() => setSelecting(null), []);
  const [voiding, setVoiding] = useState<number | null>(null);
  const [voidResult, setVoidResult] = useState<VoidResult | null>(null);
  // 改成付現的結果：成功講要付多少現金、失敗講原因（後端擋下時）。
  const [convertNotice, setConvertNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const filters = { type, voided, from, to, q: submittedSearch, page };
  const list = useQuery({
    queryKey: ["acquisitions", filters],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/acquisitions", {
        params: {
          query: {
            ...(type ? { type } : {}),
            ...(voided !== null ? { voided } : {}),
            ...(from ? { date_from: startOfDay(from) } : {}),
            ...(to ? { date_to: exclusiveEnd(to) } : {}),
            ...(submittedSearch ? { q: submittedSearch } : {}),
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        },
      });
      if (!data) throw new Error(errorDetail(error) ?? "讀取收購紀錄失敗");
      return data;
    },
    // 能不能作廢會隨外部狀態變（例如另一個分頁剛開帳）：切回來就重抓，另有「重新整理」鈕。
    refetchOnWindowFocus: true,
  });

  function resetPage<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setPage(0);
    };
  }

  const rows = list.data?.items ?? [];

  return (
    <div className="card acq-records">
      <div className="acq-records-toolbar">
        <div className="settle-tabs" aria-label="收購類型篩選">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f.label}
              type="button"
              className={`chip ${type === f.key ? "chip-active" : ""}`}
              onClick={() => resetPage(setType)(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="settle-tabs" aria-label="作廢狀態篩選">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.label}
              type="button"
              className={`chip ${voided === f.key ? "chip-active" : ""}`}
              onClick={() => resetPage(setVoided)(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="acq-records-filters">
          <label>
            起始日期
            <input type="date" value={from} onChange={(e) => resetPage(setFrom)(e.target.value)} />
          </label>
          <label>
            結束日期
            <input type="date" value={to} onChange={(e) => resetPage(setTo)(e.target.value)} />
          </label>
          <form
            className="member-allsearch"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(0);
              setSubmittedSearch(search.trim());
            }}
          >
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="賣方姓名或電話"
              aria-label="賣方搜尋"
            />
            <button type="submit" className="btn-secondary">
              搜尋
            </button>
            {submittedSearch && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  setSearch("");
                  setSubmittedSearch("");
                  setPage(0);
                }}
              >
                清除（{submittedSearch}）
              </button>
            )}
          </form>
          <button
            type="button"
            className="btn-ghost"
            disabled={list.isFetching}
            onClick={() => void list.refetch()}
          >
            {list.isFetching ? "更新中…" : "重新整理"}
          </button>
        </div>
      </div>

      {voidResult !== null && (
        <div className="form-success acq-void-result" role="status">
          <p>
            {voidResult.fully_voided === false ? "已作廢所選商品，其餘商品保留；收購單" : "已作廢收購單"} #{voidResult.acquisition_id}。退回現金{" "}
            <strong className="money">{ntd(voidResult.reversed_cash)}</strong>、沖回購物金{" "}
            <strong className="money">{ntd(voidResult.reversed_credit)}</strong>。
          </p>
        </div>
      )}

      {convertNotice !== null && (
        <p role={convertNotice.ok ? "status" : "alert"} className={convertNotice.ok ? "form-success" : "form-error"}>
          {convertNotice.text}
        </p>
      )}

      {list.isPending ? (
        <p>載入中…</p>
      ) : list.isError ? (
        <p role="alert" className="form-error">
          {list.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="empty-state">沒有符合的收購紀錄。</p>
      ) : (
        <div className="acq-records-wrap">
          <table className="data-table acq-records-table">
            <thead>
              <tr>
                <th>單號</th>
                <th>時間</th>
                <th>賣方</th>
                <th>類型</th>
                <th>品項</th>
                <th>付款</th>
                <th>經手人</th>
                <th>狀態</th>
                {isManager && <th>管理</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={row.voided_at ? "acq-records-voided" : undefined}>
                  <td>#{row.id}</td>
                  <td>{formatTaipeiDateTime(row.created_at)}</td>
                  <td>{row.seller_name || "—"}</td>
                  <td>{ACQ_TYPE_LABEL[row.type]}</td>
                  <td>
                    {itemsText(row)}
                    {row.pending_listing_count > 0 && (
                      <span className="row-sub acq-records-pending">
                        還有 {row.pending_listing_count} 件待整理（還沒上架）
                      </span>
                    )}
                  </td>
                  <td className="money">{payoutText(row)}</td>
                  <td>{row.clerk_name ?? "—"}</td>
                  <td>
                    {row.voided_at ? (
                      <span className="inv-badge inv-tone-muted">
                        已作廢 {formatTaipeiDateTime(row.voided_at)}
                      </span>
                    ) : (
                      <span className="inv-badge inv-tone-ok">有效</span>
                    )}
                  </td>
                  {isManager && (
                    <td>
                      <RecordVoidCell
                        row={row}
                        onVoid={(mode) => {
                          setVoidResult(null);
                          setConvertNotice(null);
                          if (mode === "WHOLE") setVoiding(row.id);
                          else setSelecting({ id: row.id, preselectAll: mode === "SELECT_ALL" });
                        }}
                      />
                      {canConvertToCash(row) && (
                        <ConvertToCashAction
                          row={row}
                          onDone={(text) => {
                            setVoidResult(null);
                            setConvertNotice({ ok: true, text });
                            void queryClient.invalidateQueries({ queryKey: ["acquisitions"] });
                          }}
                          onError={(text) => setConvertNotice({ ok: false, text })}
                        />
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!list.isPending && !list.isError && (
        <Pagination
          page={page}
          count={rows.length}
          pageSize={PAGE_SIZE}
          total={list.data.total}
          unit="張"
          onPage={setPage}
        />
      )}

      {selecting !== null && (
        <SelectVoidDialog
          key={selecting.id}
          acquisitionId={selecting.id}
          preselectAll={selecting.preselectAll}
          onClose={closeSelecting}
          onVoided={() => {
            void queryClient.invalidateQueries({ queryKey: ["acquisitions"] });
          }}
        />
      )}

      {voiding !== null && (
        <VoidConfirmDialog
          acquisitionId={voiding}
          onClose={() => setVoiding(null)}
          onVoided={(result) => {
            setVoiding(null);
            setConvertNotice(null);
            setVoidResult(result);
            void queryClient.invalidateQueries({ queryKey: ["acquisitions"] });
          }}
        />
      )}
    </div>
  );
}
