"use client";
// 重複規則的輸入（docs/99 §4.2）：不重複／每週／每月／每幾個月／每年。
// 不做「每天」：每天的事放開店前檢查。
import { PY_WEEKDAY_LABELS, RECURRENCE_KIND_LABEL, recurrenceSummary } from "@/features/agenda/calendar";
import { buildRule, type RuleDraft } from "@/features/agenda/forms";

const KINDS = ["NONE", "WEEKLY", "MONTHLY", "EVERY_N_MONTHS", "YEARLY"] as const;

export function RecurrenceEditor({
  draft,
  onChange,
  allowNone = true,
  disabled = false,
}: {
  draft: RuleDraft;
  onChange: (next: RuleDraft) => void;
  /** 修改重複待辦時不能改成「不重複」（要改成單次請刪掉重建）。 */
  allowNone?: boolean;
  disabled?: boolean;
}) {
  const set = (patch: Partial<RuleDraft>) => onChange({ ...draft, ...patch });
  const { rule, error } = buildRule(draft);

  return (
    <fieldset className="agenda-rule" disabled={disabled}>
      <legend className="field-label">重複</legend>
      <div className="agenda-chip-row" role="radiogroup" aria-label="重複方式">
        {KINDS.filter((k) => allowNone || k !== "NONE").map((kind) => (
          <button
            key={kind}
            type="button"
            role="radio"
            aria-checked={draft.kind === kind}
            className={`chip${draft.kind === kind ? " chip-active" : ""}`}
            onClick={() => set({ kind })}
          >
            {kind === "NONE" ? "不重複" : RECURRENCE_KIND_LABEL[kind]}
          </button>
        ))}
      </div>

      {draft.kind === "WEEKLY" && (
        <div className="agenda-chip-row" aria-label="星期幾">
          {PY_WEEKDAY_LABELS.map((label, day) => {
            const on = draft.weekdays.includes(day);
            return (
              <button
                key={label}
                type="button"
                aria-pressed={on}
                className={`chip agenda-weekday${on ? " chip-active" : ""}`}
                onClick={() =>
                  set({
                    weekdays: on
                      ? draft.weekdays.filter((d) => d !== day)
                      : [...draft.weekdays, day],
                  })
                }
              >
                週{label}
              </button>
            );
          })}
        </div>
      )}

      {draft.kind === "EVERY_N_MONTHS" && (
        <label className="field agenda-inline-field">
          <span className="field-label">每幾個月</span>
          <input
            inputMode="numeric"
            value={draft.intervalMonths}
            onChange={(e) => set({ intervalMonths: e.target.value })}
          />
        </label>
      )}
      {draft.kind === "YEARLY" && (
        <label className="field agenda-inline-field">
          <span className="field-label">幾月</span>
          <input
            inputMode="numeric"
            value={draft.monthOfYear}
            onChange={(e) => set({ monthOfYear: e.target.value })}
          />
        </label>
      )}
      {(draft.kind === "MONTHLY" || draft.kind === "EVERY_N_MONTHS" || draft.kind === "YEARLY") && (
        <label className="field agenda-inline-field">
          <span className="field-label">幾號</span>
          <input
            inputMode="numeric"
            value={draft.dayOfMonth}
            onChange={(e) => set({ dayOfMonth: e.target.value })}
          />
        </label>
      )}
      {draft.kind !== "NONE" && (
        <label className="field agenda-inline-field">
          <span className="field-label">重複到哪天為止（不填＝一直重複）</span>
          <input type="date" value={draft.until} onChange={(e) => set({ until: e.target.value })} />
        </label>
      )}

      {error !== null ? (
        <p className="form-error">{error}</p>
      ) : (
        rule !== null && <p className="hint">{recurrenceSummary(rule)}</p>
      )}
    </fieldset>
  );
}
