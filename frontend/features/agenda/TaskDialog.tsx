"use client";
// 新增待辦（單次或重複），或修改一筆單次待辦（docs/99 §4.1）。
// 重複的待辦改規則請用 SeriesDialog；只改某一次請用 OccurrenceDialog。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AGENDA_KEY, errorMessage, useAgendaDelete, useStaffMembers } from "@/features/agenda/api";
import type { AgendaTask } from "@/features/agenda/calendar";
import { DialogShell } from "@/features/agenda/DialogShell";
import { buildRule, emptyRuleDraft } from "@/features/agenda/forms";
import { RecurrenceEditor } from "@/features/agenda/RecurrenceEditor";
import {
  TaskFields,
  TitleField,
  taskDraftFrom,
  taskDraftValues,
} from "@/features/agenda/TaskFields";
import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import { taipeiDate } from "@/lib/datetime";
import { useCurrentRole } from "@/lib/useCurrentRole";

export function TaskDialog({
  task,
  defaultDate,
  onClose,
}: {
  /** 有值＝修改這筆單次待辦；沒有＝新增。 */
  task?: AgendaTask;
  defaultDate?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { isManager } = useCurrentRole();
  const staff = useStaffMembers();
  const [draft, setDraft] = useState(() => taskDraftFrom(task));
  const [dueOn, setDueOn] = useState(task?.due_on ?? defaultDate ?? "");
  const [rule, setRule] = useState(emptyRuleDraft);
  const [startsOn, setStartsOn] = useState(defaultDate ?? taipeiDate());
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useAgendaDelete();
  const repeating = task === undefined && rule.kind !== "NONE";

  const save = useMutation({
    mutationFn: async () => {
      const common = taskDraftValues(draft);
      if (common.error !== null) throw new Error(common.error);
      if (repeating) {
        const built = buildRule(rule);
        if (built.error !== null || built.rule === null) throw new Error(built.error ?? "請選重複方式");
        if (startsOn === "") throw new Error("請選從哪天開始");
        const { data, error } = await api.POST("/api/v1/agenda/task-series", {
          body: { ...common.values, starts_on: startsOn, recurrence: built.rule },
        });
        if (!data) throw new Error(errorMessage(error, "儲存失敗"));
        return;
      }
      if (common.values.due_time !== null && dueOn === "") throw new Error("有時間就要選日期");
      const body = { ...common.values, due_on: dueOn === "" ? null : dueOn };
      const { data, error } =
        task === undefined
          ? await api.POST("/api/v1/agenda/tasks", { body })
          : await api.PATCH("/api/v1/agenda/tasks/{task_id}", {
              params: { path: { task_id: task.id } },
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
  const busy = save.isPending || remove.isPending;

  return (
    <DialogShell title={task ? "修改待辦" : "新增待辦"} busy={busy} onClose={onClose}>
      <form
        className="agenda-form"
        onSubmit={(e) => {
          e.preventDefault();
          setFormError(null);
          save.mutate();
        }}
      >
        <TitleField value={draft.title} onChange={(title) => setDraft({ ...draft, title })} />
        {repeating ? (
          <label className="field">
            <span className="field-label">從哪天開始</span>
            <input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
          </label>
        ) : (
          <label className="field">
            <span className="field-label">哪天要做（不填＝放進「沒有日期」）</span>
            <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
          </label>
        )}
        {task === undefined && <RecurrenceEditor draft={rule} onChange={setRule} />}
        <TaskFields
          draft={draft}
          onChange={setDraft}
          staff={staff.data ?? []}
          isManager={isManager}
        />
        {task?.status && task.status !== "OPEN" && (
          <p className="hint">
            {task.status === "DONE" ? "已完成" : "已略過"}
            {task.resolved_by_name ? `（${task.resolved_by_name}）` : ""}
          </p>
        )}
        {formError !== null && (
          <p role="alert" className="form-error">
            {formError}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={busy}>
            {save.isPending ? "儲存中…" : "儲存"}
          </button>
          {task !== undefined && (
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
      {confirmDelete && task !== undefined && (
        <ConfirmDialog
          title="刪除這筆待辦？"
          body={<p>「{task.title}」會從行事曆拿掉。</p>}
          confirmLabel="刪除"
          danger
          busy={remove.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() =>
            remove.mutate(
              { kind: "task", id: task.id },
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
