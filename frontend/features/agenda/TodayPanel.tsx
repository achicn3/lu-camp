"use client";
// 「今天」區塊（docs/99 §8）：首頁與開店前檢查共用。
// 只顯示，不擋任何事（裁示 13：開店前檢查不要求待辦做完）。點名稱到行事曆那一天。
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAgendaToday, useItemStatus, useSkipOverdue } from "@/features/agenda/api";
import { AgendaRow } from "@/features/agenda/AgendaRow";
import {
  type AgendaItem,
  formatDayHeading,
  groupOverdue,
  openFirst,
  type PendingToggle,
  pendingDoneFor,
} from "@/features/agenda/calendar";

export function TodayPanel({
  heading = "今天",
  wide = false,
}: {
  heading?: string;
  /** 首頁用整排寬度；開店前檢查跟其他卡片一樣寬。 */
  wide?: boolean;
}) {
  const router = useRouter();
  const today = useAgendaToday();
  const status = useItemStatus();
  const skip = useSkipOverdue();
  const [actionError, setActionError] = useState<string | null>(null);
  const busy = status.isPending || skip.isPending;
  const pending: PendingToggle =
    status.isPending && status.variables
      ? { key: status.variables.item.key, done: status.variables.status === "DONE" }
      : null;

  const toggle = (item: AgendaItem, done: boolean) => {
    setActionError(null);
    status.mutate(
      { item, status: done ? "DONE" : "OPEN" },
      { onError: (err) => setActionError(err.message) },
    );
  };
  const open = (item: AgendaItem) => router.push(`/calendar?day=${item.day}`);
  const data = today.data;
  const empty =
    data !== undefined &&
    data.due_today.length + data.overdue.length + data.upcoming.length + data.events.length +
      data.campaigns.length ===
      0 &&
    data.consignment_pending_count === 0;

  return (
    <section className={`card agenda-today${wide ? " agenda-today-wide" : ""}`} aria-label={heading}>
      <div className="agenda-today-head">
        <h2>
          {heading}
          {data && <span className="agenda-today-date">{formatDayHeading(data.today)}</span>}
        </h2>
        <Link className="btn-ghost" href="/calendar">
          打開行事曆
        </Link>
      </div>
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
      {today.isPending && <p className="hint">載入中…</p>}
      {empty && <p className="hint">今天沒有待辦，也沒有快到期的事。</p>}

      {data && data.events.length + data.campaigns.length > 0 && (
        <ul className="agenda-list">
          {[...data.events, ...data.campaigns].map((item) => (
            <AgendaRow key={item.key} item={item} onOpen={open} />
          ))}
        </ul>
      )}

      {data && data.overdue.length > 0 && (
        <>
          <h3 className="agenda-today-sub agenda-today-sub-danger">已過期</h3>
          <ul className="agenda-list">
            {groupOverdue(data.overdue).map((group) =>
              group.seriesId !== null && group.items.length > 1 ? (
                <li key={group.key} className="agenda-row agenda-tone-overdue">
                  <span className="agenda-dot" aria-hidden="true" />
                  <div className="agenda-row-main">
                    <span className="agenda-row-title">
                      {group.title}：{group.items.length} 次沒打勾
                    </span>
                    <span className="agenda-row-meta">
                      {group.items.map((i) => formatDayHeading(i.day)).join("、")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={busy}
                    onClick={() => {
                      setActionError(null);
                      skip.mutate(group.seriesId ?? 0, {
                        onError: (err) => setActionError(err.message),
                      });
                    }}
                  >
                    都不用做了
                  </button>
                </li>
              ) : (
                group.items.map((item) => (
                  <AgendaRow
                    key={item.key}
                    item={item}
                    busy={busy}
                    pendingDone={pendingDoneFor(pending, item)}
                    showDate
                    onToggle={toggle}
                    onOpen={open}
                  />
                ))
              ),
            )}
          </ul>
        </>
      )}

      {data && data.due_today.length > 0 && (
        <>
          <h3 className="agenda-today-sub">今天要做</h3>
          <ul className="agenda-list">
            {openFirst(data.due_today).map((item) => (
              <AgendaRow
                key={item.key}
                item={item}
                busy={busy}
                pendingDone={pendingDoneFor(pending, item)}
                onToggle={toggle}
                onOpen={open}
              />
            ))}
          </ul>
        </>
      )}

      {data && data.upcoming.length > 0 && (
        <>
          <h3 className="agenda-today-sub">快到期</h3>
          <ul className="agenda-list">
            {data.upcoming.map((item) => (
              <AgendaRow
                key={item.key}
                item={item}
                busy={busy}
                pendingDone={pendingDoneFor(pending, item)}
                showDate
                onToggle={toggle}
                onOpen={open}
              />
            ))}
          </ul>
        </>
      )}

      {data && data.consignment_pending_count > 0 && (
        <p className="agenda-today-consign">
          寄售品賣掉後，還有 {data.consignment_pending_count} 筆錢沒撥給寄售人。
          <Link href="/consignment">去寄售付款</Link>
        </p>
      )}
    </section>
  );
}
