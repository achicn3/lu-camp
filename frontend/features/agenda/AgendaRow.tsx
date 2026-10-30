"use client";
// 行事曆上的一列（單日清單、未來清單、「今天」共用）。
// 待辦前面有勾選框（做完打勾、按錯可取消）；點名稱打開詳細。活動只是標示，點了到活動頁。
import Link from "next/link";

import {
  type AgendaItem,
  EVENT_KIND_LABEL,
  formatDayHeading,
  itemLabel,
  itemTone,
} from "@/features/agenda/calendar";

export function AgendaRow({
  item,
  busy = false,
  pendingDone = null,
  showDate = false,
  onToggle,
  onOpen,
}: {
  item: AgendaItem;
  busy?: boolean;
  /** 剛按下、伺服器還沒回應：先照按的樣子顯示，不要讓勾選框「按了沒反應」。 */
  pendingDone?: boolean | null;
  /** 跨好幾天的清單（快到期）才需要在列上寫日期。 */
  showDate?: boolean;
  onToggle?: (item: AgendaItem, done: boolean) => void;
  onOpen?: (item: AgendaItem) => void;
}) {
  const tone = itemTone(item);
  const isTask = item.kind === "TASK";
  const done = pendingDone ?? item.status === "DONE";
  const meta: string[] = [];
  if (showDate) meta.push(formatDayHeading(item.day));
  // 名稱就是「公休」時不必再標一次類型。
  if (item.kind === "EVENT" && item.event_kind && item.title !== EVENT_KIND_LABEL[item.event_kind]) {
    meta.push(EVENT_KIND_LABEL[item.event_kind]);
  }
  if (item.end_day) meta.push(`到 ${formatDayHeading(item.end_day)}`);
  if (item.assignee_name) meta.push(item.assignee_name);
  if (item.series_id !== null && item.series_id !== undefined) meta.push("重複");
  if (item.status === "SKIPPED") meta.push("這次不用做");
  if (item.manager_only) meta.push("只有管理者看得到");

  const label = itemLabel(item);
  const title =
    item.href && !isTask && item.kind !== "EVENT" ? (
      <Link className="agenda-row-title" href={item.href}>
        {label}
      </Link>
    ) : onOpen && item.kind !== "CAMPAIGN_START" && item.kind !== "CAMPAIGN_END" ? (
      <button type="button" className="agenda-row-title" onClick={() => onOpen(item)}>
        {label}
      </button>
    ) : (
      <span className="agenda-row-title">{label}</span>
    );

  return (
    <li className={`agenda-row agenda-tone-${tone}`}>
      {isTask && onToggle ? (
        <input
          type="checkbox"
          className="agenda-check"
          aria-label={`${item.title}${done ? "（已完成）" : ""}`}
          checked={done}
          disabled={busy || item.status === "SKIPPED"}
          onChange={(e) => onToggle(item, e.target.checked)}
        />
      ) : (
        <span className="agenda-dot" aria-hidden="true" />
      )}
      <div className="agenda-row-main">
        {title}
        {(meta.length > 0 || item.overdue) && (
          <span className="agenda-row-meta">
            {item.overdue && <span className="agenda-tag-overdue">已過期</span>}
            {meta.join("・")}
          </span>
        )}
        {item.note && <span className="agenda-row-note">{item.note}</span>}
      </div>
      {isTask && item.href && (
        <Link className="btn-ghost agenda-row-go" href={item.href}>
          前往處理
        </Link>
      )}
    </li>
  );
}
