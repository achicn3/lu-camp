"use client";
// 官網資料欄位（上架、保留、顏色、露數、缺件、瑕疵、配件、影片）：現貨與預售共用（docs/97 §6、docs/102）。
// 缺件、瑕疵、配件用多行文字框，一行一項（官網照行列出）。
import type { components } from "@/lib/api-types";

import { linesToList, listToLines } from "@/features/consignment-tents/tentText";

type ListingBody = components["schemas"]["TentListingWriteRequest"];

export interface ListingDraft {
  published: boolean;
  reserved: boolean;
  color: string;
  nights: string;
  missing: string;
  defects: string;
  extras: string;
  video: string;
}

export function draftFrom(listing: Partial<ListingBody> | null): ListingDraft {
  return {
    published: listing?.published ?? false,
    reserved: listing?.reserved ?? false,
    color: listing?.color ?? "",
    nights:
      listing?.nights === null || listing?.nights === undefined ? "" : String(listing.nights),
    missing: listToLines(listing?.missing ?? []),
    defects: listToLines(listing?.defects ?? []),
    extras: listToLines(listing?.extras ?? []),
    video: listing?.video_url ?? "",
  };
}

function parseNights(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,4}$/.test(trimmed)) throw new Error("使用過幾露請填整數（沒用過填 0）");
  return Number(trimmed);
}

/** 送出用的內容；露數不是整數丟 Error。 */
export function bodyFrom(draft: ListingDraft): ListingBody {
  return {
    published: draft.published,
    reserved: draft.reserved,
    color: draft.color.trim() === "" ? null : draft.color.trim(),
    nights: parseNights(draft.nights),
    missing: linesToList(draft.missing),
    defects: linesToList(draft.defects),
    extras: linesToList(draft.extras),
    video_url: draft.video.trim() === "" ? null : draft.video.trim(),
  };
}

export function ListingToggles({
  draft,
  onChange,
}: {
  draft: ListingDraft;
  onChange: (next: ListingDraft) => void;
}) {
  return (
    <div className="tents-toggles">
      <label className="field-toggle">
        <input
          type="checkbox"
          checked={draft.published}
          onChange={(e) => onChange({ ...draft, published: e.target.checked })}
        />
        上架官網
      </label>
      <label className="field-toggle">
        <input
          type="checkbox"
          checked={draft.reserved}
          onChange={(e) => onChange({ ...draft, reserved: e.target.checked })}
        />
        保留中（官網顯示「已有人保留」，不擋結帳）
      </label>
    </div>
  );
}

export function ListingFields({
  draft,
  onChange,
}: {
  draft: ListingDraft;
  onChange: (next: ListingDraft) => void;
}) {
  const set = (patch: Partial<ListingDraft>) => onChange({ ...draft, ...patch });
  return (
    <>
      <div className="tents-grid">
        <label className="field">
          <span className="field-label">顏色</span>
          <input
            value={draft.color}
            maxLength={30}
            onChange={(e) => set({ color: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">使用過幾露</span>
          <input
            inputMode="numeric"
            placeholder="沒用過填 0"
            value={draft.nights}
            onChange={(e) => set({ nights: e.target.value })}
          />
        </label>
      </div>

      <label className="field tents-textarea">
        <span className="field-label">缺件（一行一項，沒有就留空）</span>
        <textarea rows={2} value={draft.missing} onChange={(e) => set({ missing: e.target.value })} />
      </label>
      <label className="field tents-textarea">
        <span className="field-label">瑕疵（一行一處，寫清楚位置，沒有就留空）</span>
        <textarea rows={3} value={draft.defects} onChange={(e) => set({ defects: e.target.value })} />
      </label>
      <label className="field tents-textarea">
        <span className="field-label">額外配件（原廠標配以外附的東西，一行一件）</span>
        <textarea rows={2} value={draft.extras} onChange={(e) => set({ extras: e.target.value })} />
      </label>
      <label className="field">
        <span className="field-label">搭帳影片（選填，YouTube 網址，可設為不公開）</span>
        <input
          inputMode="url"
          placeholder="https://youtu.be/…"
          value={draft.video}
          onChange={(e) => set({ video: e.target.value })}
        />
      </label>
    </>
  );
}
