"use client";
// 編輯一頂寄售帳篷的官網資料（docs/97 §6）。照片上傳／排序／刪除當下就存；其他欄位按「儲存」才送。
// 缺件、瑕疵、配件用多行文字框，一行一項（官網照行列出）。
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { linesToList, listToLines } from "@/features/consignment-tents/tentText";
import { MAX_PHOTO_BYTES, menuPhotoUrl } from "@/features/menu/menuPhoto";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Tent = components["schemas"]["TentListingRead"];

const MAX_PHOTOS = 8;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // 422 驗證錯誤：取第一則訊息給店員看。
    if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === "string") {
      return String(detail[0].msg).replace(/^Value error, /, "");
    }
  }
  return null;
}

function parseNights(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,4}$/.test(trimmed)) return "invalid";
  return Number(trimmed);
}

function TentPhotos({
  tent,
  onChange,
}: {
  tent: Tent;
  onChange: (next: Tent) => void;
}) {
  const inputId = useId();
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      let latest: Tent | null = null;
      // 一張一張傳：照片順序就是挑選順序，而且第 9 張會被擋下、前面的照樣存好。
      for (const file of files) {
        if (file.size > MAX_PHOTO_BYTES) throw new Error(`${file.name} 超過 10 MB，請先縮小再上傳`);
        const { data, error: err } = await api.POST(
          "/api/v1/online-order/tents/{item_id}/photos",
          {
            params: { path: { item_id: tent.item_id } },
            body: { file: "" },
            bodySerializer: () => {
              const form = new FormData();
              form.append("file", file, file.name);
              return form;
            },
          },
        );
        if (!data) throw new Error(extractDetail(err) ?? "上傳照片失敗");
        latest = data;
        onChange(data);
      }
      return latest;
    },
    onSuccess: () => setError(null),
    onError: (err: Error) => setError(err.message),
  });

  const reorder = useMutation({
    mutationFn: async (photos: string[]) => {
      const { data, error: err } = await api.PUT(
        "/api/v1/online-order/tents/{item_id}/photos/order",
        { params: { path: { item_id: tent.item_id } }, body: { photos } },
      );
      if (!data) throw new Error(extractDetail(err) ?? "調整順序失敗");
      return data;
    },
    onSuccess: (data) => {
      setError(null);
      onChange(data);
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: async (sha256: string) => {
      const { data, error: err } = await api.DELETE(
        "/api/v1/online-order/tents/{item_id}/photos/{sha256}",
        { params: { path: { item_id: tent.item_id, sha256 } } },
      );
      if (!data) throw new Error(extractDetail(err) ?? "刪除照片失敗");
      return data;
    },
    onSuccess: (data) => {
      setError(null);
      onChange(data);
    },
    onError: (err: Error) => setError(err.message),
  });

  function move(index: number, delta: number) {
    const next = [...tent.photos];
    const [picked] = next.splice(index, 1);
    if (picked === undefined) return;
    next.splice(index + delta, 0, picked);
    reorder.mutate(next);
  }

  const busy = upload.isPending || reorder.isPending || remove.isPending;
  const full = tent.photos.length >= MAX_PHOTOS;
  return (
    <fieldset className="tents-photos">
      <legend className="field-label">
        照片（{tent.photos.length}/{MAX_PHOTOS}，第一張是封面；建議順序：搭好全景、內部空間、收納狀態、額外配件、瑕疵特寫）
      </legend>
      <ol className="tents-photo-list">
        {tent.photos.map((sha, index) => (
          <li key={sha} className="tents-photo">
            {/* eslint-disable-next-line @next/next/no-img-element -- 後端已轉好 WebP，不經 next/image 最佳化 */}
            <img src={menuPhotoUrl(sha)} alt={`第 ${index + 1} 張`} loading="lazy" />
            <div className="tents-photo-actions">
              <button
                type="button"
                className="btn-ghost"
                aria-label={`第 ${index + 1} 張往前`}
                disabled={busy || index === 0}
                onClick={() => move(index, -1)}
              >
                ←
              </button>
              <button
                type="button"
                className="btn-ghost"
                aria-label={`第 ${index + 1} 張往後`}
                disabled={busy || index === tent.photos.length - 1}
                onClick={() => move(index, 1)}
              >
                →
              </button>
              <button
                type="button"
                className="btn-ghost btn-danger-text"
                aria-label={`刪除第 ${index + 1} 張`}
                disabled={busy}
                onClick={() => remove.mutate(sha)}
              >
                刪除
              </button>
            </div>
          </li>
        ))}
      </ol>
      <label
        htmlFor={inputId}
        className={`btn-secondary tents-photo-upload${busy || full ? " is-disabled" : ""}`}
      >
        {upload.isPending ? "上傳中…" : full ? "已達 8 張" : "加照片"}
      </label>
      <input
        id={inputId}
        className="sr-only"
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
        aria-label="加照片"
        disabled={busy || full}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = ""; // 同一個檔再選一次也要觸發
          if (files.length > 0) upload.mutate(files.slice(0, MAX_PHOTOS - tent.photos.length));
        }}
      />
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function TentEditDialog({
  tent: initial,
  onClose,
  onSaved,
  onPhotosChanged,
}: {
  tent: Tent;
  onClose: () => void;
  onSaved: () => void;
  onPhotosChanged: () => void;
}) {
  const [tent, setTent] = useState(initial);
  const [published, setPublished] = useState(initial.published);
  const [reserved, setReserved] = useState(initial.reserved);
  const [color, setColor] = useState(initial.color ?? "");
  const [nights, setNights] = useState(initial.nights === null ? "" : String(initial.nights));
  const [missing, setMissing] = useState(listToLines(initial.missing ?? []));
  const [defects, setDefects] = useState(listToLines(initial.defects ?? []));
  const [extras, setExtras] = useState(listToLines(initial.extras ?? []));
  const [video, setVideo] = useState(initial.video_url ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const parsed = parseNights(nights);
      if (parsed === "invalid") throw new Error("使用過幾露請填整數（沒用過填 0）");
      const { data, error: err } = await api.PUT("/api/v1/online-order/tents/{item_id}", {
        params: { path: { item_id: tent.item_id } },
        body: {
          published,
          reserved,
          color: color.trim() === "" ? null : color.trim(),
          nights: parsed,
          missing: linesToList(missing),
          defects: linesToList(defects),
          extras: linesToList(extras),
          video_url: video.trim() === "" ? null : video.trim(),
        },
      });
      if (!data) throw new Error(extractDetail(err) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => onSaved(),
    onError: (err: Error) => setError(err.message),
  });

  const title = [tent.brand_name, tent.name].filter(Boolean).join(" ");
  return (
    <div
      className="pos-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`編輯 ${tent.item_code} 官網資料`}
    >
      <form
        className="card pos-dialog tents-dialog"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          save.mutate();
        }}
      >
        <h2>
          {title}
          <span className="tents-dialog-code">{tent.item_code}</span>
        </h2>

        <div className="tents-toggles">
          <label className="field-toggle">
            <input
              type="checkbox"
              checked={published}
              onChange={(e) => setPublished(e.target.checked)}
            />
            上架官網
          </label>
          <label className="field-toggle">
            <input
              type="checkbox"
              checked={reserved}
              onChange={(e) => setReserved(e.target.checked)}
            />
            保留中（官網顯示「已有人保留」，不擋結帳）
          </label>
        </div>

        <div className="tents-grid">
          <label className="field">
            <span className="field-label">顏色</span>
            <input value={color} maxLength={30} onChange={(e) => setColor(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">使用過幾露</span>
            <input
              inputMode="numeric"
              placeholder="沒用過填 0"
              value={nights}
              onChange={(e) => setNights(e.target.value)}
            />
          </label>
        </div>

        <label className="field tents-textarea">
          <span className="field-label">缺件（一行一項，沒有就留空）</span>
          <textarea rows={2} value={missing} onChange={(e) => setMissing(e.target.value)} />
        </label>
        <label className="field tents-textarea">
          <span className="field-label">瑕疵（一行一處，寫清楚位置，沒有就留空）</span>
          <textarea rows={3} value={defects} onChange={(e) => setDefects(e.target.value)} />
        </label>
        <label className="field tents-textarea">
          <span className="field-label">額外配件（原廠標配以外附的東西，一行一件）</span>
          <textarea rows={2} value={extras} onChange={(e) => setExtras(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">搭帳影片（YouTube 網址，可設為不公開）</span>
          <input
            inputMode="url"
            placeholder="https://youtu.be/…"
            value={video}
            onChange={(e) => setVideo(e.target.value)}
          />
        </label>

        <TentPhotos
          tent={tent}
          onChange={(next) => {
            setTent(next);
            onPhotosChanged();
          }}
        />

        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={save.isPending}>
            {save.isPending ? "儲存中…" : "儲存"}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </div>
  );
}
