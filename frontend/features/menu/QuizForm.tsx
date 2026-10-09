"use client";
// 「不知道喝什麼」引導推薦的設定（docs/63 §2 M2a；店主 2026-10-09）：1–3 題、每題 2–4 個答案，
// 每個答案勾「適合的品項」（菜單品項或手沖體驗卡）。客人答完，被勾到最多次的最推薦、其次兩個備選。
// 只引用既有品項，不填價格；下架或售完的客人頁自動略過。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { apiDetail } from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Quiz = components["schemas"]["MenuQuizRead"];
type Ref = components["schemas"]["QuizItemRef"];
// 表單內一律有 items（API 型別因預設值是選填）。
type Option = { label: string; items: Ref[] };
type Question = { prompt: string; options: Option[] };
type MenuItem = components["schemas"]["MenuItemRead"];
type Experience = components["schemas"]["MenuExperienceRead"];

const MAX_QUESTIONS = 3;
const MIN_OPTIONS = 2;
const MAX_OPTIONS = 4;

const refKey = (ref: Ref) => `${ref.kind}:${ref.id}`;

function parseRef(value: string): Ref | null {
  const [kind, id] = value.split(":");
  const num = Number(id);
  if ((kind !== "item" && kind !== "experience") || !Number.isInteger(num) || num <= 0) return null;
  return { kind, id: num };
}

export function QuizForm({
  initial,
  items,
  experiences,
  onDone,
  onCancel,
}: {
  initial: Quiz;
  items: MenuItem[];
  experiences: Experience[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [active, setActive] = useState(initial.is_active);
  const [questions, setQuestions] = useState<Question[]>(() =>
    initial.questions.map((q) => ({
      prompt: q.prompt,
      options: q.options.map((o) => ({ label: o.label, items: o.items ?? [] })),
    })),
  );
  const [error, setError] = useState<string | null>(null);

  const choices = [
    ...items.map((item) => ({ key: `item:${item.id}`, label: item.name })),
    ...experiences.map((exp) => ({ key: `experience:${exp.id}`, label: `手沖體驗：${exp.title}` })),
  ];
  const labelOf = (ref: Ref) =>
    choices.find((choice) => choice.key === refKey(ref))?.label ?? "已移除的品項";

  const updateQuestion = (qi: number, change: (q: Question) => Question) =>
    setQuestions((list) => list.map((q, i) => (i === qi ? change(q) : q)));
  const updateOption = (qi: number, oi: number, change: (o: Option) => Option) =>
    updateQuestion(qi, (q) => ({ ...q, options: q.options.map((o, i) => (i === oi ? change(o) : o)) }));

  const blank = questions.some((q) => q.prompt.trim() === "" || q.options.some((o) => o.label.trim() === ""));
  const ticked = questions.some((q) => q.options.some((o) => o.items.length > 0));

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        is_active: active,
        questions: questions.map((q) => ({
          prompt: q.prompt.trim(),
          options: q.options.map((o) => ({ label: o.label.trim(), items: o.items })),
        })),
      };
      const { data, error: e } = await api.PUT("/api/v1/online-order/quiz", { body });
      if (!data) throw new Error(apiDetail(e, "儲存失敗"));
      return data;
    },
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["menu-quiz"] });
      onDone();
    },
    onError: (reason: Error) => setError(reason.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!blank) save.mutate();
  }

  return (
    <form className="exp-form quiz-form" aria-label="引導推薦" onSubmit={submit}>
      <fieldset className="card exp-block" disabled={save.isPending}>
        <label className="field-toggle">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          在客人頁顯示「不知道喝什麼？」
        </label>
        {active && !ticked && (
          <p className="hint">還沒勾任何品項，客人頁不會出現這個入口。</p>
        )}
      </fieldset>

      {questions.map((question, qi) => (
        <fieldset key={qi} className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">第 {qi + 1} 題</legend>
          <label className="field">
            <span className="field-label">題目</span>
            <input
              aria-label={`題目 ${qi + 1}`}
              value={question.prompt}
              maxLength={30}
              onChange={(e) => updateQuestion(qi, (q) => ({ ...q, prompt: e.target.value }))}
            />
          </label>
          {question.options.map((option, oi) => {
            const name = `第 ${qi + 1} 題答案 ${oi + 1}`;
            const picked = new Set(option.items.map(refKey));
            return (
              <div key={oi} role="group" aria-label={name} className="quiz-answer">
                <div className="quiz-answer-head">
                  <input
                    aria-label={name}
                    value={option.label}
                    maxLength={20}
                    onChange={(e) => updateOption(qi, oi, (o) => ({ ...o, label: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="btn-ghost"
                    aria-label={`移除${name}`}
                    disabled={question.options.length <= MIN_OPTIONS}
                    onClick={() =>
                      updateQuestion(qi, (q) => ({ ...q, options: q.options.filter((_, i) => i !== oi) }))
                    }
                  >
                    移除
                  </button>
                </div>
                <ul className="quiz-picks">
                  {option.items.map((ref) => (
                    <li key={refKey(ref)}>
                      <span>{labelOf(ref)}</span>
                      <button
                        type="button"
                        className="btn-ghost"
                        aria-label={`移除 ${labelOf(ref)}（${name}）`}
                        onClick={() =>
                          updateOption(qi, oi, (o) => ({
                            ...o,
                            items: o.items.filter((r) => refKey(r) !== refKey(ref)),
                          }))
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
                <select
                  aria-label={`${name} 加入品項`}
                  value=""
                  onChange={(e) => {
                    const ref = parseRef(e.target.value);
                    if (ref !== null) updateOption(qi, oi, (o) => ({ ...o, items: [...o.items, ref] }));
                  }}
                >
                  <option value="">＋ 勾一個適合的品項</option>
                  {choices
                    .filter((choice) => !picked.has(choice.key))
                    .map((choice) => (
                      <option key={choice.key} value={choice.key}>
                        {choice.label}
                      </option>
                    ))}
                </select>
              </div>
            );
          })}
          <div className="quiz-question-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={question.options.length >= MAX_OPTIONS}
              onClick={() =>
                updateQuestion(qi, (q) => ({ ...q, options: [...q.options, { label: "", items: [] }] }))
              }
            >
              ＋ 第 {qi + 1} 題加一個答案
            </button>
            <button
              type="button"
              className="btn-ghost pur-cancel-btn"
              disabled={questions.length <= 1}
              onClick={() => setQuestions((list) => list.filter((_, i) => i !== qi))}
            >
              刪除第 {qi + 1} 題
            </button>
          </div>
        </fieldset>
      ))}

      <button
        type="button"
        className="btn-secondary"
        disabled={questions.length >= MAX_QUESTIONS || save.isPending}
        onClick={() =>
          setQuestions((list) => [
            ...list,
            { prompt: "", options: [{ label: "", items: [] }, { label: "", items: [] }] },
          ])
        }
      >
        ＋ 加一題
      </button>

      {blank && <p className="hint">每一題和每個答案都要有文字。</p>}
      {error !== null && <p role="alert" className="form-error">{error}</p>}
      <div className="exp-actions">
        <button type="submit" className="btn-primary" disabled={blank || save.isPending}>
          {save.isPending ? "儲存中…" : "儲存"}
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
}
