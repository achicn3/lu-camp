"use client";
// 手沖體驗卡的新增／編輯表單（獨立頁；docs/63 §4、M1c）。右側即時預覽客人翻開後看到的卡面。
// 卡片只引用既有品項＋預選選項：售價由原品項加預選選項算，這裡不填價格。
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  ARTS,
  type Art,
  DEFAULT_INCLUDES,
  EFFECTS,
  type Effect,
  MAX_INCLUDES,
  THEMES,
  type Theme,
  apiDetail,
} from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type Experience = components["schemas"]["MenuExperienceRead"];
type Write = components["schemas"]["MenuExperienceWriteRequest"];
type MenuItem = components["schemas"]["MenuItemRead"];

export function ExperienceForm({ initial, items, onDone, onCancel }: {
  initial: Experience | null;
  items: MenuItem[];
  onDone: (saved: Experience) => void;
  onCancel: () => void;
}) {
  const [itemId, setItemId] = useState<number | null>(initial?.menu_item_id ?? null);
  const [optionIds, setOptionIds] = useState<number[]>(initial?.option_ids ?? []);
  const [title, setTitle] = useState(initial?.title ?? "");
  const [tag, setTag] = useState(initial?.tag ?? "");
  const [origin, setOrigin] = useState(initial?.origin ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [includes, setIncludes] = useState(
    (initial?.includes ?? DEFAULT_INCLUDES).map((i) => ({ title: i.title, detail: i.detail ?? "" })),
  );
  const [theme, setTheme] = useState<Theme>(initial?.theme ?? "peach");
  const [art, setArt] = useState<Art>(initial?.art ?? "peach");
  const [effect, setEffect] = useState<Effect>(initial?.effect ?? "random");
  const [active, setActive] = useState(initial?.is_active ?? true);
  const [sortOrder, setSortOrder] = useState(String(initial?.sort_order ?? 0));
  const [error, setError] = useState<string | null>(null);
  const item = items.find((entry) => entry.id === itemId);

  const save = useMutation({
    mutationFn: async (body: Write) => {
      const result = initial === null
        ? await api.POST("/api/v1/online-order/experiences", { body })
        : await api.PUT("/api/v1/online-order/experiences/{experience_id}", {
          params: { path: { experience_id: initial.id } }, body,
        });
      if (!result.data) throw new Error(apiDetail(result.error, "儲存體驗卡失敗"));
      return result.data;
    },
    onSuccess: onDone,
    onError: (reason: Error) => setError(reason.message),
  });

  // 售價：原品項＋預選選項；還沒預選的必選群組要客人自己選（可能再加價）。
  const preset = new Set(optionIds);
  const base = parseNtd(item?.unit_price ?? "0") ?? 0;
  const presetTotal = (item?.option_groups ?? []).flatMap((g) => g.options)
    .filter((o) => preset.has(o.id)).reduce((sum, o) => sum + (parseNtd(o.price_delta) ?? 0), 0);
  const pending = (item?.option_groups ?? [])
    .filter((g) => g.options.filter((o) => preset.has(o.id)).length < g.min_select)
    .map((g) => g.name);
  const price = `$${formatNtd(base + presetTotal)}${pending.length ? " 起" : ""}`;

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (itemId === null) { setError("請選原品項"); return; }
    if (title.trim() === "") { setError("請填卡片標題"); return; }
    const order = Number(sortOrder);
    if (!Number.isInteger(order) || order < 0 || order > 9999) { setError("排序請填 0–9999 的整數"); return; }
    save.mutate({
      menu_item_id: itemId,
      option_ids: optionIds,
      title: title.trim(),
      tag: tag.trim() || null,
      origin: origin.trim() || null,
      notes: notes.trim() || null,
      description: description.trim() || null,
      includes: includes.filter((i) => i.title.trim() !== "").map((i) => ({ title: i.title.trim(), detail: i.detail.trim() || null })),
      theme,
      art,
      effect,
      is_active: active,
      sort_order: order,
    });
  }

  return (
    <form className="exp-form" aria-label="體驗卡" onSubmit={submit}>
      <div className="exp-form-main">
        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">品項與價格</legend>
          <label className="field">
            <span className="field-label">原品項</span>
            <select value={itemId ?? ""} onChange={(e) => { setItemId(e.target.value ? Number(e.target.value) : null); setOptionIds([]); }}>
              <option value="">請選擇（例如手沖咖啡）</option>
              {items.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
            </select>
          </label>
          {item && item.option_groups.length > 0 && (
            <div className="exp-groups">
              {item.option_groups.map((group) => (
                <fieldset key={group.id} className="exp-group">
                  <legend>預選{group.name}{group.min_select > 0 ? `（必選 ${group.min_select}）` : ""}</legend>
                  {group.options.map((option) => {
                    const delta = parseNtd(option.price_delta) ?? 0;
                    return (
                      <label key={option.id} className="field-toggle">
                        <input
                          type="checkbox"
                          checked={preset.has(option.id)}
                          onChange={(e) => setOptionIds((ids) => e.target.checked ? [...ids, option.id] : ids.filter((id) => id !== option.id))}
                        />
                        <span>{option.name}{delta > 0 ? `（+$${formatNtd(delta)}）` : ""}</span>
                      </label>
                    );
                  })}
                </fieldset>
              ))}
            </div>
          )}
          {item && (
            <p className="hint">
              售價 ${formatNtd(base + presetTotal)}（原品項＋預選選項）
              {pending.length ? `；客人還要自己選：${pending.join("、")}` : ""}
            </p>
          )}
        </fieldset>

        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">卡面文字</legend>
          <label className="field"><span className="field-label">卡片標題</span><input value={title} maxLength={30} placeholder="例如：蜜桃蹦蹦手沖體驗" onChange={(e) => setTitle(e.target.value)} /></label>
          <div className="exp-grid">
            <label className="field"><span className="field-label">標籤</span><input value={tag} maxLength={12} placeholder="例如：清甜果香" onChange={(e) => setTag(e.target.value)} /></label>
            <label className="field"><span className="field-label">產地／處理法</span><input value={origin} maxLength={60} placeholder="例如：柯契爾｜水洗" onChange={(e) => setOrigin(e.target.value)} /></label>
          </div>
          <label className="field"><span className="field-label">風味</span><input value={notes} maxLength={80} placeholder="例如：水蜜桃・白桃・荔枝" onChange={(e) => setNotes(e.target.value)} /></label>
          <label className="field"><span className="field-label">介紹</span><textarea value={description} maxLength={300} rows={3} onChange={(e) => setDescription(e.target.value)} /></label>
        </fieldset>

        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">體驗包含（最多 {MAX_INCLUDES} 項）</legend>
          {includes.map((include, index) => (
            <div key={index} className="exp-include">
              <input aria-label={`包含項目 ${index + 1}`} value={include.title} maxLength={20} placeholder="例如：咖啡豆"
                onChange={(e) => setIncludes((list) => list.map((x, i) => i === index ? { ...x, title: e.target.value } : x))} />
              <input aria-label={`項目 ${index + 1} 說明`} value={include.detail} maxLength={60} placeholder="說明（選填）"
                onChange={(e) => setIncludes((list) => list.map((x, i) => i === index ? { ...x, detail: e.target.value } : x))} />
              <button type="button" className="btn-ghost" aria-label={`移除項目 ${index + 1}`} onClick={() => setIncludes((list) => list.filter((_, i) => i !== index))}>移除</button>
            </div>
          ))}
          {includes.length < MAX_INCLUDES && (
            <button type="button" className="btn-ghost exp-add-include" onClick={() => setIncludes((list) => [...list, { title: "", detail: "" }])}>＋ 加一項</button>
          )}
        </fieldset>

        <fieldset className="card exp-block" disabled={save.isPending}>
          <legend className="exp-block-title">外觀與動畫</legend>
          <div className="exp-themes" role="radiogroup" aria-label="卡面配色">
            {THEMES.map((option) => (
              <label key={option.value} className={`exp-theme${theme === option.value ? " is-on" : ""}`}>
                <input type="radio" name="experience-theme" checked={theme === option.value} onChange={() => setTheme(option.value)} />
                <span className={`menu-experience-swatch brew-swatch-${option.value}`} aria-hidden="true" />
                {option.label}
              </label>
            ))}
          </div>
          <div className="exp-grid">
            <label className="field">
              <span className="field-label">插畫</span>
              <select value={art} onChange={(e) => setArt(e.target.value as Art)}>
                {ARTS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">抽卡動畫</span>
              <select value={effect} onChange={(e) => setEffect(e.target.value as Effect)}>
                {EFFECTS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">排序（小的在前）</span>
              <input type="number" min={0} max={9999} value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
            </label>
          </div>
          <label className="field-toggle"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /><span>啟用（發佈後客人看得到）</span></label>
        </fieldset>
      </div>

      <aside className="exp-form-side">
        <p className="field-label">客人翻開後看到的卡面</p>
        <article className={`exp-preview exp-theme-${theme}`} aria-label="卡面預覽">
          {tag.trim() && <span className="exp-preview-tag">{tag}</span>}
          <h3>{title.trim() || "卡片標題"}</h3>
          {origin.trim() && <p className="exp-preview-origin">{origin}</p>}
          {notes.trim() && <p className="exp-preview-notes">{notes}</p>}
          {description.trim() && <p className="exp-preview-desc">{description}</p>}
          {art !== "none" && (
            // eslint-disable-next-line @next/next/no-img-element -- 已壓好的 480px 靜態 JPG，不需要最佳化管線
            <img className="exp-preview-art" src={`/brew/${art}.jpg`} alt="" width={240} height={240} />
          )}
          <div className="exp-preview-foot"><span>手沖體驗</span><strong>{item ? price : "—"}</strong></div>
        </article>
        <p className="hint">抽卡動畫要在線上菜單上看（發佈後用手機掃 QR）。</p>
        {error !== null && <p role="alert" className="form-error">{error}</p>}
        <div className="exp-actions">
          <button type="submit" className="btn-primary" disabled={save.isPending}>{save.isPending ? "儲存中…" : "儲存體驗卡"}</button>
          <button type="button" className="btn-ghost" disabled={save.isPending} onClick={onCancel}>取消</button>
        </div>
      </aside>
    </form>
  );
}
