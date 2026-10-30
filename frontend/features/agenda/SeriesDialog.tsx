"use client";
// 修改重複的待辦（docs/99 §4.1、ADR-034 決定 6）。
// 改重複方式或開始日時：之前沒做的那幾次照舊留著提醒，新的規則從今天開始算（後端處理）。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AGENDA_KEY, errorMessage, useAgendaDelete, useStaffMembers } from "@/features/agenda/api";
import type { AgendaSeries } from "@/features/agenda/calendar";
import { DialogShell } from "@/features/agenda/DialogShell";
import { buildRule, ruleDraftFrom } from "@/features/agenda/forms";
import { RecurrenceEditor } from "@/features/agenda/RecurrenceEditor";
import {
  TaskFields,
  TitleField,
  taskDraftFrom,
  taskDraftValues,
} from "@/features/agenda/TaskFields";
import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import { useCurrentRole } from "@/lib/useCurrentRole";

export function SeriesDialog({ series, onClose }: { series: AgendaSeries; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { isManager } = useCurrentRole();
  const staff = useStaffMembers();
  const [draft, setDraft] = useState(() => taskDraftFrom(series));
  const [rule, setRule] = useState(() => ruleDraftFrom(series.recurrence));
  const [startsOn, setStartsOn] = useState(series.starts_on);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useAgendaDelete();

  const save = useMutation({
    mutationFn: async () => {
      const common = taskDraftValues(draft);
      if (common.error !== null) throw new Error(common.error);
      const built = buildRule(rule);
      if (built.error !== null || built.rule === null) throw new Error(built.error ?? "請選重複方式");
      if (startsOn === "") throw new Error("請選從哪天開始");
      const { data, error } = await api.PATCH("/api/v1/agenda/task-series/{series_id}", {
        params: { path: { series_id: series.id } },
        body: { ...common.values, starts_on: startsOn, recurrence: built.rule },
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
    <DialogShell title="修改重複的待辦" busy={busy} onClose={onClose}>
      <form
        className="agenda-form"
        onSubmit={(e) => {
          e.preventDefault();
          setFormError(null);
          save.mutate();
        }}
      >
        <TitleField value={draft.title} onChange={(title) => setDraft({ ...draft, title })} />
        <label className="field">
          <span className="field-label">從哪天開始</span>
          <input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
        </label>
        <RecurrenceEditor draft={rule} onChange={setRule} allowNone={false} />
        <p className="hint">
          改了重複方式或開始日：之前沒做的那幾次會留著提醒，新的規則從今天開始算。已經做完的不會變。
        </p>
        <TaskFields
          draft={draft}
          onChange={setDraft}
          staff={staff.data ?? []}
          isManager={isManager}
        />
        {formError !== null && (
          <p role="alert" className="form-error">
            {formError}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={busy}>
            {save.isPending ? "儲存中…" : "儲存"}
          </button>
          <button
            type="button"
            className="btn-ghost agenda-delete"
            disabled={busy}
            onClick={() => setConfirmDelete(true)}
          >
            刪除
          </button>
        </div>
      </form>
      {confirmDelete && (
        <ConfirmDialog
          title="刪除這個重複的待辦？"
          body={
            <p>
              「{series.title}」之後不會再出現，還沒做的那幾次也一起拿掉；已經做完、略過的會留著當紀錄。
            </p>
          }
          confirmLabel="刪除"
          danger
          busy={remove.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() =>
            remove.mutate(
              { kind: "series", id: series.id },
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
