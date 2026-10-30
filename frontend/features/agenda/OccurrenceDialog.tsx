"use client";
// 重複待辦的其中一次：改期、改時間、備註、負責人，或完成／略過這一次（docs/99 §4.1）。
// 名稱與重複方式屬於整個重複待辦，要改請按「修改整個重複待辦」。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AGENDA_KEY, errorMessage, useItemStatus, useStaffMembers } from "@/features/agenda/api";
import { type AgendaItem, formatDayHeading } from "@/features/agenda/calendar";
import { DialogShell } from "@/features/agenda/DialogShell";
import { StaffSelect } from "@/features/agenda/TaskFields";
import { api } from "@/lib/api";

export function OccurrenceDialog({
  item,
  onClose,
  onEditSeries,
}: {
  item: AgendaItem;
  onClose: () => void;
  onEditSeries: (seriesId: number) => void;
}) {
  const queryClient = useQueryClient();
  const staff = useStaffMembers();
  const status = useItemStatus();
  const [dueOn, setDueOn] = useState(item.day);
  const [dueTime, setDueTime] = useState(item.start_time?.slice(0, 5) ?? "");
  const [assignee, setAssignee] = useState(item.assignee_staff_id ? String(item.assignee_staff_id) : "");
  const [note, setNote] = useState(item.note ?? "");
  const [formError, setFormError] = useState<string | null>(null);
  const seriesId = item.series_id ?? 0;
  const occurrenceOn = item.occurrence_on ?? item.day;
  const done = item.status === "DONE" || item.status === "SKIPPED";

  const save = useMutation({
    mutationFn: async () => {
      if (dueOn === "") throw new Error("重複待辦的每一次都要有日期");
      const { data, error } = await api.PATCH(
        "/api/v1/agenda/task-series/{series_id}/occurrences/{occurrence_on}",
        {
          params: { path: { series_id: seriesId, occurrence_on: occurrenceOn } },
          body: {
            due_on: dueOn,
            due_time: dueTime === "" ? null : dueTime,
            note: note.trim() === "" ? null : note.trim(),
            assignee_staff_id: assignee === "" ? null : Number(assignee),
          },
        },
      );
      if (!data) throw new Error(errorMessage(error, "儲存失敗"));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: AGENDA_KEY });
      onClose();
    },
    onError: (err) => setFormError(err.message),
  });
  const busy = save.isPending || status.isPending;
  const setStatus = (next: "DONE" | "SKIPPED" | "OPEN") =>
    status.mutate(
      { item, status: next },
      { onSuccess: onClose, onError: (err) => setFormError(err.message) },
    );

  return (
    <DialogShell title={item.title} busy={busy} onClose={onClose}>
      <p className="hint">
        這是重複待辦的其中一次（原本是 {formatDayHeading(occurrenceOn)}）。在這裡改只影響這一次。
      </p>
      <form
        className="agenda-form"
        onSubmit={(e) => {
          e.preventDefault();
          setFormError(null);
          save.mutate();
        }}
      >
        <div className="agenda-form-row">
          <label className="field">
            <span className="field-label">改到哪天</span>
            <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">幾點（選填）</span>
            <input type="time" value={dueTime} onChange={(e) => setDueTime(e.target.value)} />
          </label>
        </div>
        <StaffSelect value={assignee} onChange={setAssignee} staff={staff.data ?? []} />
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
            {save.isPending ? "儲存中…" : "儲存這一次"}
          </button>
          {done ? (
            <button type="button" className="btn-ghost" disabled={busy} onClick={() => setStatus("OPEN")}>
              改回未完成
            </button>
          ) : (
            <>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => setStatus("DONE")}>
                完成
              </button>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => setStatus("SKIPPED")}>
                這次不用做
              </button>
            </>
          )}
        </div>
      </form>
      <button
        type="button"
        className="btn-ghost agenda-link-btn"
        disabled={busy}
        onClick={() => onEditSeries(seriesId)}
      >
        修改整個重複待辦（名稱、重複方式）
      </button>
    </DialogShell>
  );
}
