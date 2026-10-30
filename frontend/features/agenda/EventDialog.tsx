"use client";
// 新增或修改事件：公休、臨時店休、市集／擺攤、其他（docs/99 §4.3）。
// 事件只顯示在行事曆和「今天」，不會影響 POS 或線上點餐。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  AGENDA_KEY,
  errorMessage,
  useAgendaDelete,
  useAgendaEvent,
  useEventOccurrence,
} from "@/features/agenda/api";
import {
  type AgendaEvent,
  type AgendaEventKind,
  EVENT_KIND_LABEL,
  formatDayHeading,
} from "@/features/agenda/calendar";
import { DialogShell } from "@/features/agenda/DialogShell";
import { buildRule, emptyRuleDraft, ruleDraftFrom } from "@/features/agenda/forms";
import { RecurrenceEditor } from "@/features/agenda/RecurrenceEditor";
import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import { taipeiDate } from "@/lib/datetime";
import { useCurrentRole } from "@/lib/useCurrentRole";

const KINDS: AgendaEventKind[] = ["CLOSED", "TEMP_CLOSED", "MARKET", "OTHER"];

export function EventDialog({
  eventId,
  occurrenceDay,
  defaultDate,
  onClose,
}: {
  /** 有值＝修改；沒有＝新增。 */
  eventId?: number;
  /** 從行事曆點進來的那一次（重複事件可以只取消這一次）。 */
  occurrenceDay?: string;
  defaultDate?: string;
  onClose: () => void;
}) {
  const existing = useAgendaEvent(eventId ?? null);
  if (eventId !== undefined) {
    if (existing.isError) {
      return (
        <DialogShell title="事件" onClose={onClose}>
          <p role="alert" className="form-error">
            {existing.error.message}
          </p>
        </DialogShell>
      );
    }
    if (!existing.data) {
      return (
        <DialogShell title="事件" onClose={onClose}>
          <p className="hint">載入中…</p>
        </DialogShell>
      );
    }
    return (
      <EventForm event={existing.data} occurrenceDay={occurrenceDay} onClose={onClose} />
    );
  }
  return <EventForm defaultDate={defaultDate} onClose={onClose} />;
}

function EventForm({
  event,
  occurrenceDay,
  defaultDate,
  onClose,
}: {
  event?: AgendaEvent;
  occurrenceDay?: string;
  defaultDate?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { isManager } = useCurrentRole();
  const remove = useAgendaDelete();
  const occurrence = useEventOccurrence();
  const [kind, setKind] = useState<AgendaEventKind>(event?.kind ?? "CLOSED");
  const [title, setTitle] = useState(event?.title ?? "");
  const [startsOn, setStartsOn] = useState(event?.starts_on ?? defaultDate ?? taipeiDate());
  const [endsOn, setEndsOn] = useState(event?.ends_on ?? "");
  const [startTime, setStartTime] = useState(event?.start_time?.slice(0, 5) ?? "");
  const [endTime, setEndTime] = useState(event?.end_time?.slice(0, 5) ?? "");
  const [managerOnly, setManagerOnly] = useState(event?.manager_only ?? false);
  const [note, setNote] = useState(event?.note ?? "");
  const [rule, setRule] = useState(() =>
    event?.recurrence ? ruleDraftFrom(event.recurrence) : emptyRuleDraft(),
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = useMutation({
    mutationFn: async () => {
      if (startsOn === "") throw new Error("請選日期");
      if (endsOn !== "" && endsOn < startsOn) throw new Error("結束日不能早於開始日");
      const built = buildRule(rule);
      if (built.error !== null) throw new Error(built.error);
      const body = {
        kind,
        // 沒填名稱就用類型當名稱（「公休」本身就說清楚了）。
        title: title.trim() === "" ? EVENT_KIND_LABEL[kind] : title.trim(),
        note: note.trim() === "" ? null : note.trim(),
        starts_on: startsOn,
        ends_on: endsOn === "" ? startsOn : endsOn,
        start_time: startTime === "" ? null : startTime,
        end_time: endTime === "" ? null : endTime,
        manager_only: managerOnly,
        recurrence: built.rule,
      };
      const { data, error } =
        event === undefined
          ? await api.POST("/api/v1/agenda/events", { body })
          : await api.PATCH("/api/v1/agenda/events/{event_id}", {
              params: { path: { event_id: event.id } },
              body,
            });
      if (!data) throw new Error(errorMessage(error, "儲存失敗"));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: AGENDA_KEY });
      onClose();
    },
    onError: (err) => setFormError(err.message),
  });
  const busy = save.isPending || remove.isPending || occurrence.isPending;
  const repeating = event?.recurrence !== null && event?.recurrence !== undefined;
  const setCancelled = (on: string, cancelled: boolean) =>
    event &&
    occurrence.mutate(
      { eventId: event.id, on, cancelled },
      {
        onSuccess: () => {
          if (cancelled) onClose();
        },
        onError: (err) => setFormError(err.message),
      },
    );

  return (
    <DialogShell title={event ? "修改事件" : "新增事件"} busy={busy} onClose={onClose}>
      <form
        className="agenda-form"
        onSubmit={(e) => {
          e.preventDefault();
          setFormError(null);
          save.mutate();
        }}
      >
        <div className="agenda-chip-row" role="radiogroup" aria-label="事件類型">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              className={`chip${kind === k ? " chip-active" : ""}`}
              onClick={() => setKind(k)}
            >
              {EVENT_KIND_LABEL[k]}
            </button>
          ))}
        </div>
        <label className="field">
          <span className="field-label">名稱（不填就用「{EVENT_KIND_LABEL[kind]}」）</span>
          <input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="agenda-form-row">
          <label className="field">
            <span className="field-label">開始日</span>
            <input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">結束日（一天的不用填）</span>
            <input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
          </label>
        </div>
        <div className="agenda-form-row">
          <label className="field">
            <span className="field-label">開始時間（選填）</span>
            <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">結束時間（選填）</span>
            <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
          </label>
        </div>
        <RecurrenceEditor draft={rule} onChange={setRule} />
        {isManager && (
          <label className="field-toggle">
            <input
              type="checkbox"
              checked={managerOnly}
              onChange={(e) => setManagerOnly(e.target.checked)}
            />
            <span>只有管理者看得到</span>
          </label>
        )}
        <label className="field">
          <span className="field-label">備註（選填）</span>
          <textarea rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        {formError !== null && (
          <p role="alert" className="form-error">
            {formError}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={busy}>
            {save.isPending ? "儲存中…" : "儲存"}
          </button>
          {event !== undefined && (
            <button
              type="button"
              className="btn-ghost agenda-delete"
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              刪除
            </button>
          )}
        </div>
      </form>

      {event !== undefined && repeating && occurrenceDay !== undefined && (
        <button
          type="button"
          className="btn-secondary agenda-link-btn"
          disabled={busy}
          onClick={() => setCancelled(occurrenceDay, true)}
        >
          只取消 {formatDayHeading(occurrenceDay)} 這一次
        </button>
      )}
      {event !== undefined && event.cancelled_dates.length > 0 && (
        <div className="agenda-cancelled">
          <p className="field-label">已取消的日子</p>
          <ul>
            {event.cancelled_dates.map((day) => (
              <li key={day}>
                {formatDayHeading(day)}
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => setCancelled(day, false)}
                >
                  恢復
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {confirmDelete && event !== undefined && (
        <ConfirmDialog
          title="刪除這個事件？"
          body={<p>「{event.title}」{repeating ? "的每一次（包括以前的）都" : ""}會從行事曆拿掉。</p>}
          confirmLabel="刪除"
          danger
          busy={remove.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() =>
            remove.mutate(
              { kind: "event", id: event.id },
              {
                onSuccess: onClose,
                onError: (err) => {
                  setConfirmDelete(false);
                  setFormError(err.message);
                },
              },
            )
          }
        />
      )}
    </DialogShell>
  );
}
