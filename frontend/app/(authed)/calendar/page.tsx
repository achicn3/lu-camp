"use client";
// /calendar 行事曆（docs/99 §9）：月曆、未來兩週清單、點某一天看當天。
//
// 上面有：待辦（單次與重複）、事件（公休、市集…）、門市活動的開始與最後一天。
// 第一期 1B／1C 再加採購單的下單、到貨、付款。系統帶入的項目唯讀，點了到原本的頁面。
// 篩選記在這台裝置上（每台裝置看的東西可能不同：收銀台只看待辦、店長看全部）。
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import {
  useAgendaItems,
  useAgendaTask,
  useAgendaToday,
  useItemStatus,
  useTaskSeries,
  useTaskStatus,
  useUndatedTasks,
} from "@/features/agenda/api";
import { AgendaRow } from "@/features/agenda/AgendaRow";
import {
  type AgendaItem,
  type AgendaSeries,
  addMonths,
  formatDayHeading,
  itemLabel,
  itemTone,
  listDays,
  monthGrid,
  openFirst,
  type PendingToggle,
  pendingDoneFor,
  recurrenceSummary,
  spreadByDay,
  WEEK_HEADER,
} from "@/features/agenda/calendar";
import { EventDialog } from "@/features/agenda/EventDialog";
import { OccurrenceDialog } from "@/features/agenda/OccurrenceDialog";
import { SeriesDialog } from "@/features/agenda/SeriesDialog";
import { StaffDialog } from "@/features/agenda/StaffDialog";
import { TaskDialog } from "@/features/agenda/TaskDialog";
import { DialogShell } from "@/features/agenda/DialogShell";
import { shiftIsoDate, taipeiDate } from "@/lib/datetime";
import { useCurrentRole } from "@/lib/useCurrentRole";

type View = "month" | "list";
type Filters = { task: boolean; event: boolean; campaign: boolean; showDone: boolean };
type DialogState =
  | { type: "new-task"; date?: string }
  | { type: "edit-task"; taskId: number }
  | { type: "occurrence"; item: AgendaItem }
  | { type: "series"; series: AgendaSeries }
  | { type: "new-event"; date?: string }
  | { type: "event"; eventId: number; day: string }
  | { type: "staff" }
  | null;

const LIST_DAYS = 14;
const CELL_CHIPS = 3;
const FILTER_KEY = "lu-camp.calendar.filters";
const VIEW_KEY = "lu-camp.calendar.view";
const DEFAULT_FILTERS: Filters = { task: true, event: true, campaign: true, showDone: false };
const FILTER_LABELS: { key: keyof Omit<Filters, "showDone">; label: string }[] = [
  { key: "task", label: "待辦" },
  { key: "event", label: "事件" },
  { key: "campaign", label: "活動" },
];
const LEGEND: { tone: string; label: string }[] = [
  { tone: "task", label: "待辦" },
  { tone: "overdue", label: "已過期" },
  { tone: "closed", label: "公休／店休" },
  { tone: "market", label: "市集" },
  { tone: "other", label: "其他事件" },
  { tone: "campaign", label: "門市活動" },
];

function isIsoDate(value: string | null): value is string {
  return value !== null && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

// 記在瀏覽器只是方便：讀不到（無痕、封鎖）就用預設，不影響使用。
function loadStored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 存不了就算了：下次打開回到預設篩選。
  }
}

function initialView(): View {
  try {
    const saved = window.localStorage.getItem(VIEW_KEY);
    if (saved === "month" || saved === "list") return saved;
  } catch {
    // 讀不到就依螢幕寬度決定。
  }
  // 平板、手機的月曆格子太小，預設用清單（docs/99 §9）。
  return window.matchMedia?.("(max-width: 900px)").matches ? "list" : "month";
}

// 「已完成」只在月曆格子裡預設藏起來（格子放不下）；當天清單與清單看法一律列出、劃掉、排在後面，
// 打勾之後才看得到自己做了什麼。
function passes(item: AgendaItem, filters: Filters, where: "grid" | "list"): boolean {
  if (item.kind === "TASK") {
    if (!filters.task) return false;
    return where === "list" || filters.showDone || item.status === "OPEN";
  }
  if (item.kind === "EVENT") return filters.event;
  return filters.campaign;
}

export default function CalendarPage() {
  // useSearchParams 需要 Suspense 邊界（Next App Router）。
  return (
    <Suspense fallback={<p>載入中…</p>}>
      <CalendarView />
    </Suspense>
  );
}

function CalendarView() {
  const params = useSearchParams();
  const today = taipeiDate();
  const requested = params.get("day");
  const firstDay = isIsoDate(requested) ? requested : today;
  const { isManager } = useCurrentRole();
  const [view, setView] = useState<View>(initialView);
  const [cursor, setCursor] = useState(() => ({
    year: Number(firstDay.slice(0, 4)),
    month: Number(firstDay.slice(5, 7)),
  }));
  const [selected, setSelected] = useState(firstDay);
  const [listStart, setListStart] = useState(today);
  const [filters, setFilters] = useState<Filters>(() => loadStored(FILTER_KEY, DEFAULT_FILTERS));
  const [dialog, setDialog] = useState<DialogState>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const status = useItemStatus();
  const series = useTaskSeries();
  const pending: PendingToggle =
    status.isPending && status.variables
      ? { key: status.variables.item.key, done: status.variables.status === "DONE" }
      : null;

  const changeView = (next: View) => {
    setView(next);
    store(VIEW_KEY, next);
  };
  const changeFilters = (next: Filters) => {
    setFilters(next);
    store(FILTER_KEY, next);
  };
  const toggle = (item: AgendaItem, done: boolean) => {
    setActionError(null);
    status.mutate(
      { item, status: done ? "DONE" : "OPEN" },
      { onError: (err) => setActionError(err.message) },
    );
  };
  const open = (item: AgendaItem) => {
    if (item.kind === "EVENT" && item.event_id) {
      setDialog({ type: "event", eventId: item.event_id, day: item.day });
    } else if (item.kind === "TASK" && item.series_id) {
      setDialog({ type: "occurrence", item });
    } else if (item.kind === "TASK" && item.task_id) {
      setDialog({ type: "edit-task", taskId: item.task_id });
    }
  };
  const editSeries = (seriesId: number) => {
    const found = series.data?.find((s) => s.id === seriesId);
    setDialog(found ? { type: "series", series: found } : null);
  };

  return (
    <section className="agenda-page">
      <div className="agenda-page-head">
        <h1 className="page-title">行事曆</h1>
        <div className="agenda-page-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => setDialog({ type: "new-task", date: view === "month" ? selected : today })}
          >
            ＋ 新增待辦
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setDialog({ type: "new-event", date: view === "month" ? selected : today })}
          >
            ＋ 新增事件
          </button>
          {isManager && (
            <button type="button" className="btn-ghost" onClick={() => setDialog({ type: "staff" })}>
              員工名單
            </button>
          )}
        </div>
      </div>

      <div className="agenda-toolbar">
        <div className="agenda-chip-row" role="radiogroup" aria-label="看法">
          {(["month", "list"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={view === v}
              className={`chip${view === v ? " chip-active" : ""}`}
              onClick={() => changeView(v)}
            >
              {v === "month" ? "月曆" : "未來兩週"}
            </button>
          ))}
        </div>
        <div className="agenda-chip-row" aria-label="要顯示的項目">
          {FILTER_LABELS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              aria-pressed={filters[key]}
              className={`chip${filters[key] ? " chip-active" : ""}`}
              onClick={() => changeFilters({ ...filters, [key]: !filters[key] })}
            >
              {label}
            </button>
          ))}
          <label className="field-toggle">
            <input
              type="checkbox"
              checked={filters.showDone}
              onChange={(e) => changeFilters({ ...filters, showDone: e.target.checked })}
            />
            <span>月曆格子也顯示已完成</span>
          </label>
        </div>
      </div>
      <ul className="agenda-legend" aria-label="顏色說明">
        {LEGEND.map(({ tone, label }) => (
          <li key={tone} className={`agenda-tone-${tone}`}>
            <span className="agenda-dot" aria-hidden="true" />
            {label}
          </li>
        ))}
      </ul>

      {actionError !== null && (
        <p role="alert" className="form-error">
          {actionError}
        </p>
      )}

      {view === "month" ? (
        <MonthView
          year={cursor.year}
          month={cursor.month}
          today={today}
          selected={selected}
          filters={filters}
          busy={status.isPending}
          pending={pending}
          onMonth={(delta) => setCursor(addMonths(cursor.year, cursor.month, delta))}
          onToday={() => {
            setCursor({ year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) });
            setSelected(today);
          }}
          onSelect={setSelected}
          onToggle={toggle}
          onOpen={open}
          onAdd={(type, date) => setDialog({ type, date })}
        />
      ) : (
        <ListView
          start={listStart}
          today={today}
          filters={filters}
          busy={status.isPending}
          pending={pending}
          onShift={(days) => setListStart(days === 0 ? today : shiftIsoDate(listStart, days))}
          onToggle={toggle}
          onOpen={open}
        />
      )}

      <div className="agenda-lower">
        <UndatedCard onEdit={(taskId) => setDialog({ type: "edit-task", taskId })} />
        <SeriesCard
          series={series.data ?? []}
          error={series.isError ? series.error.message : null}
          onEdit={(s) => setDialog({ type: "series", series: s })}
        />
      </div>

      {dialog?.type === "new-task" && (
        <TaskDialog defaultDate={dialog.date} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "edit-task" && (
        <TaskEditLoader taskId={dialog.taskId} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "occurrence" && (
        <OccurrenceDialog item={dialog.item} onClose={() => setDialog(null)} onEditSeries={editSeries} />
      )}
      {dialog?.type === "series" && (
        <SeriesDialog series={dialog.series} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "new-event" && (
        <EventDialog defaultDate={dialog.date} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "event" && (
        <EventDialog eventId={dialog.eventId} occurrenceDay={dialog.day} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "staff" && <StaffDialog onClose={() => setDialog(null)} />}
    </section>
  );
}

function MonthView({
  year,
  month,
  today,
  selected,
  filters,
  busy,
  pending,
  onMonth,
  onToday,
  onSelect,
  onToggle,
  onOpen,
  onAdd,
}: {
  year: number;
  month: number;
  today: string;
  selected: string;
  filters: Filters;
  busy: boolean;
  pending: PendingToggle;
  onMonth: (delta: number) => void;
  onToday: () => void;
  onSelect: (day: string) => void;
  onToggle: (item: AgendaItem, done: boolean) => void;
  onOpen: (item: AgendaItem) => void;
  onAdd: (type: "new-task" | "new-event", date: string) => void;
}) {
  const grid = monthGrid(year, month);
  const items = useAgendaItems(grid.start, grid.end);
  const byDay = useMemo(
    () =>
      spreadByDay(
        (items.data ?? []).filter((i) => passes(i, filters, "grid")),
        grid.start,
        grid.end,
      ),
    [items.data, filters, grid.start, grid.end],
  );
  const dayItems = useMemo(
    () =>
      openFirst(
        spreadByDay(
          (items.data ?? []).filter((i) => passes(i, filters, "list")),
          selected,
          selected,
        ).get(selected) ?? [],
      ),
    [items.data, filters, selected],
  );
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;

  return (
    <div className="agenda-month">
      <div className="agenda-month-nav">
        <button type="button" className="btn-ghost" aria-label="上個月" onClick={() => onMonth(-1)}>
          ‹
        </button>
        <h2>
          {year} 年 {month} 月
        </h2>
        <button type="button" className="btn-ghost" aria-label="下個月" onClick={() => onMonth(1)}>
          ›
        </button>
        <button type="button" className="btn-ghost" onClick={onToday}>
          今天
        </button>
      </div>
      {items.isError && (
        <p role="alert" className="form-error">
          {items.error.message}
        </p>
      )}
      <div className="agenda-month-body">
        <div className="agenda-grid" aria-label={`${year} 年 ${month} 月`}>
          <div className="agenda-grid-row agenda-grid-head" aria-hidden="true">
            {WEEK_HEADER.map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          {grid.weeks.map((week) => (
            <div key={week[0]} className="agenda-grid-row">
              {week.map((day) => {
                const list = byDay.get(day) ?? [];
                const classes = ["agenda-cell"];
                if (!day.startsWith(monthPrefix)) classes.push("agenda-cell-outside");
                if (day === today) classes.push("agenda-cell-today");
                if (day === selected) classes.push("agenda-cell-selected");
                return (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={day === selected}
                    aria-label={`${formatDayHeading(day)}，${list.length} 項`}
                    className={classes.join(" ")}
                    onClick={() => onSelect(day)}
                  >
                    <span className="agenda-cell-date">{Number(day.slice(8))}</span>
                    {list.slice(0, CELL_CHIPS).map((item) => (
                      <span key={item.key} className={`agenda-chip agenda-tone-${itemTone(item)}`}>
                        {itemLabel(item)}
                      </span>
                    ))}
                    {list.length > CELL_CHIPS && (
                      <span className="agenda-cell-more">還有 {list.length - CELL_CHIPS} 項</span>
                    )}
                    {list.length > 0 && <span className="agenda-cell-count">{list.length}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <aside className="card agenda-day" aria-label={`${formatDayHeading(selected)} 的項目`}>
          <div className="agenda-day-head">
            <h2>{formatDayHeading(selected)}</h2>
            <div className="agenda-day-actions">
              <button type="button" className="btn-ghost" onClick={() => onAdd("new-task", selected)}>
                ＋待辦
              </button>
              <button type="button" className="btn-ghost" onClick={() => onAdd("new-event", selected)}>
                ＋事件
              </button>
            </div>
          </div>
          {items.isPending && <p className="hint">載入中…</p>}
          {items.isSuccess && dayItems.length === 0 && <p className="hint">這天沒有安排。</p>}
          <ul className="agenda-list">
            {dayItems.map((item) => (
              <AgendaRow
                key={item.key}
                item={item}
                busy={busy}
                pendingDone={pendingDoneFor(pending, item)}
                onToggle={onToggle}
                onOpen={onOpen}
              />
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

function ListView({
  start,
  today,
  filters,
  busy,
  pending,
  onShift,
  onToggle,
  onOpen,
}: {
  start: string;
  today: string;
  filters: Filters;
  busy: boolean;
  pending: PendingToggle;
  onShift: (days: number) => void;
  onToggle: (item: AgendaItem, done: boolean) => void;
  onOpen: (item: AgendaItem) => void;
}) {
  const end = shiftIsoDate(start, LIST_DAYS - 1);
  const items = useAgendaItems(start, end);
  const overdue = useAgendaToday();
  const byDay = useMemo(
    () => spreadByDay((items.data ?? []).filter((i) => passes(i, filters, "list")), start, end),
    [items.data, filters, start, end],
  );
  const days = listDays(start, LIST_DAYS).filter((day) => day === today || byDay.has(day));
  const overdueItems = start === today && filters.task ? (overdue.data?.overdue ?? []) : [];

  return (
    <div className="agenda-listview">
      <div className="agenda-month-nav">
        <button type="button" className="btn-ghost" aria-label="往前兩週" onClick={() => onShift(-LIST_DAYS)}>
          ‹
        </button>
        <h2>
          {formatDayHeading(start)} ～ {formatDayHeading(end)}
        </h2>
        <button type="button" className="btn-ghost" aria-label="往後兩週" onClick={() => onShift(LIST_DAYS)}>
          ›
        </button>
        <button type="button" className="btn-ghost" onClick={() => onShift(0)}>
          今天
        </button>
      </div>
      {items.isError && (
        <p role="alert" className="form-error">
          {items.error.message}
        </p>
      )}
      {overdueItems.length > 0 && (
        <div className="card agenda-listday">
          <h3 className="agenda-today-sub agenda-today-sub-danger">已過期</h3>
          <ul className="agenda-list">
            {overdueItems.map((item) => (
              <AgendaRow
                key={item.key}
                item={item}
                busy={busy}
                pendingDone={pendingDoneFor(pending, item)}
                showDate
                onToggle={onToggle}
                onOpen={onOpen}
              />
            ))}
          </ul>
        </div>
      )}
      {items.isPending && <p className="hint">載入中…</p>}
      {days.map((day) => {
        const list = openFirst(byDay.get(day) ?? []);
        return (
          <div key={day} className="card agenda-listday">
            <h3 className="agenda-today-sub">
              {formatDayHeading(day)}
              {day === today && <span className="agenda-today-date">今天</span>}
            </h3>
            {list.length === 0 ? (
              <p className="hint">這天沒有安排。</p>
            ) : (
              <ul className="agenda-list">
                {list.map((item) => (
                  <AgendaRow
                    key={item.key}
                    item={item}
                    busy={busy}
                    pendingDone={pendingDoneFor(pending, item)}
                    onToggle={onToggle}
                    onOpen={onOpen}
                  />
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function UndatedCard({ onEdit }: { onEdit: (taskId: number) => void }) {
  const undated = useUndatedTasks();
  const status = useTaskStatus();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="card">
      <h2>沒有日期的待辦</h2>
      <p className="hint">想到要做、但還沒排日子的事放這裡。</p>
      {(undated.isError || error !== null) && (
        <p role="alert" className="form-error">
          {error ?? undated.error?.message}
        </p>
      )}
      {undated.data?.length === 0 && <p className="hint">目前沒有。</p>}
      <ul className="agenda-list">
        {(undated.data ?? []).map((task) => (
          <li key={task.id} className="agenda-row agenda-tone-task">
            <input
              type="checkbox"
              className="agenda-check"
              aria-label={task.title}
              checked={
                status.isPending && status.variables?.taskId === task.id
                  ? status.variables.status === "DONE"
                  : task.status === "DONE"
              }
              disabled={status.isPending}
              onChange={(e) => {
                setError(null);
                status.mutate(
                  { taskId: task.id, status: e.target.checked ? "DONE" : "OPEN" },
                  { onError: (err) => setError(err.message) },
                );
              }}
            />
            <div className="agenda-row-main">
              <button type="button" className="agenda-row-title" onClick={() => onEdit(task.id)}>
                {task.title}
              </button>
              {task.assignee_name && <span className="agenda-row-meta">{task.assignee_name}</span>}
            </div>
            {task.link_href && (
              <Link className="btn-ghost agenda-row-go" href={task.link_href}>
                前往處理
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SeriesCard({
  series,
  error,
  onEdit,
}: {
  series: AgendaSeries[];
  error: string | null;
  onEdit: (series: AgendaSeries) => void;
}) {
  return (
    <div className="card">
      <h2>重複的待辦</h2>
      <p className="hint">固定要做的事（付房租、叫貨、保養…）。每天都要做的，放開店前檢查就好。</p>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {series.length === 0 && error === null && <p className="hint">還沒有重複的待辦。</p>}
      <ul className="agenda-list">
        {series.map((s) => (
          <li key={s.id} className="agenda-row agenda-tone-task">
            <span className="agenda-dot" aria-hidden="true" />
            <div className="agenda-row-main">
              <button type="button" className="agenda-row-title" onClick={() => onEdit(s)}>
                {s.title}
              </button>
              <span className="agenda-row-meta">
                {recurrenceSummary(s.recurrence)}
                {s.next_on ? `・下一次 ${formatDayHeading(s.next_on)}` : "・已經結束"}
                {s.assignee_name ? `・${s.assignee_name}` : ""}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TaskEditLoader({ taskId, onClose }: { taskId: number; onClose: () => void }) {
  const task = useAgendaTask(taskId);
  if (task.data) return <TaskDialog task={task.data} onClose={onClose} />;
  return (
    <DialogShell title="修改待辦" onClose={onClose}>
      {task.isError ? (
        <p role="alert" className="form-error">
          {task.error.message}
        </p>
      ) : (
        <p className="hint">載入中…</p>
      )}
    </DialogShell>
  );
}
