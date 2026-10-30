"use client";
// 待辦與重複待辦共用的欄位：標題、時間、負責人、提前幾天提醒、只有管理者看得到、備註。
// 日期（或重複的開始日）由各個視窗自己放：單次與重複的意思不一樣。
import type { AgendaSeries, AgendaTask, StaffMember } from "@/features/agenda/calendar";
import { parseLeadDays } from "@/features/agenda/forms";

export type TaskDraft = {
  title: string;
  dueTime: string;
  assignee: string;
  lead: string;
  managerOnly: boolean;
  note: string;
};

export function taskDraftFrom(source?: AgendaTask | AgendaSeries): TaskDraft {
  return {
    title: source?.title ?? "",
    dueTime: source?.due_time?.slice(0, 5) ?? "",
    assignee: source?.assignee_staff_id ? String(source.assignee_staff_id) : "",
    lead: String(source?.lead_days ?? 3),
    managerOnly: source?.manager_only ?? false,
    note: source?.note ?? "",
  };
}

/** 共用欄位轉成 API 的值；有錯回 error。 */
export function taskDraftValues(draft: TaskDraft) {
  const leadDays = parseLeadDays(draft.lead);
  if (draft.title.trim() === "") return { error: "請填要做什麼" } as const;
  if (leadDays === null) return { error: "提前幾天提醒要填 0 到 365" } as const;
  return {
    error: null,
    values: {
      title: draft.title.trim(),
      note: draft.note.trim() === "" ? null : draft.note.trim(),
      due_time: draft.dueTime === "" ? null : draft.dueTime,
      assignee_staff_id: draft.assignee === "" ? null : Number(draft.assignee),
      manager_only: draft.managerOnly,
      lead_days: leadDays,
    },
  } as const;
}

export function StaffSelect({
  value,
  onChange,
  staff,
}: {
  value: string;
  onChange: (value: string) => void;
  staff: StaffMember[];
}) {
  // 已停用的負責人仍要顯示得出名字（歷史待辦），但不讓人再選。
  const known = staff.some((m) => String(m.id) === value);
  return (
    <label className="field">
      <span className="field-label">負責人（選填）</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">不指定</option>
        {staff.map((m) => (
          <option key={m.id} value={String(m.id)}>
            {m.name}
          </option>
        ))}
        {value !== "" && !known && (
          <option value={value} disabled>
            （已停用的員工）
          </option>
        )}
      </select>
    </label>
  );
}

export function TitleField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span className="field-label">要做什麼</span>
      <input
        value={value}
        maxLength={100}
        placeholder="例：回覆廠商報價、付房租"
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** 標題以外的共用欄位：時間、提前天數、負責人、只有管理者看得到、備註（標題與日期由視窗自己排在最前面）。 */
export function TaskFields({
  draft,
  onChange,
  staff,
  isManager,
}: {
  draft: TaskDraft;
  onChange: (next: TaskDraft) => void;
  staff: StaffMember[];
  isManager: boolean;
}) {
  const set = (patch: Partial<TaskDraft>) => onChange({ ...draft, ...patch });
  return (
    <>
      <div className="agenda-form-row">
        <label className="field">
          <span className="field-label">幾點（選填）</span>
          <input type="time" value={draft.dueTime} onChange={(e) => set({ dueTime: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">提前幾天提醒</span>
          <input
            inputMode="numeric"
            value={draft.lead}
            onChange={(e) => set({ lead: e.target.value })}
          />
        </label>
      </div>
      <p className="hint">到期前這幾天，會出現在首頁的「快到期」。</p>
      <StaffSelect value={draft.assignee} onChange={(assignee) => set({ assignee })} staff={staff} />
      {isManager && (
        <label className="field-toggle">
          <input
            type="checkbox"
            checked={draft.managerOnly}
            onChange={(e) => set({ managerOnly: e.target.checked })}
          />
          <span>只有管理者看得到</span>
        </label>
      )}
      <label className="field">
        <span className="field-label">備註（選填）</span>
        <textarea
          rows={2}
          maxLength={1000}
          value={draft.note}
          onChange={(e) => set({ note: e.target.value })}
        />
      </label>
    </>
  );
}
