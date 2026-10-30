"use client";
// 帳篷照片（docs/97 §6、docs/102）：上傳／排序／刪除當下就存。現貨與預售共用，打哪支端點由呼叫端給。
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { MAX_PHOTO_BYTES, menuPhotoUrl } from "@/features/menu/menuPhoto";

export const MAX_PHOTOS = 8;

/** 三個動作都回「最新的照片清單」；失敗丟 Error（訊息直接給店員看）。 */
export interface PhotoOps {
  upload: (file: File) => Promise<string[]>;
  reorder: (photos: string[]) => Promise<string[]>;
  remove: (sha256: string) => Promise<string[]>;
}

export function extractDetail(error: unknown): string | null {
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

/** 照片表單上傳：openapi-fetch 預設送 JSON，這裡換成 multipart。 */
export function photoForm(file: File) {
  return {
    body: { file: "" },
    bodySerializer: () => {
      const form = new FormData();
      form.append("file", file, file.name);
      return form;
    },
  };
}

export function TentPhotos({
  photos,
  ops,
  onChange,
}: {
  photos: string[];
  ops: PhotoOps;
  onChange: (next: string[]) => void;
}) {
  const inputId = useId();
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      // 一張一張傳：照片順序就是挑選順序，而且第 9 張會被擋下、前面的照樣存好。
      for (const file of files) {
        if (file.size > MAX_PHOTO_BYTES) throw new Error(`${file.name} 超過 10 MB，請先縮小再上傳`);
        onChange(await ops.upload(file));
      }
    },
    onSuccess: () => setError(null),
    onError: (err: Error) => setError(err.message),
  });

  const reorder = useMutation({
    mutationFn: ops.reorder,
    onSuccess: (next) => {
      setError(null);
      onChange(next);
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: ops.remove,
    onSuccess: (next) => {
      setError(null);
      onChange(next);
    },
    onError: (err: Error) => setError(err.message),
  });

  function move(index: number, delta: number) {
    const next = [...photos];
    const [picked] = next.splice(index, 1);
    if (picked === undefined) return;
    next.splice(index + delta, 0, picked);
    reorder.mutate(next);
  }

  const busy = upload.isPending || reorder.isPending || remove.isPending;
  const full = photos.length >= MAX_PHOTOS;
  return (
    <fieldset className="tents-photos">
      <legend className="field-label">
        照片（選填，{photos.length}/{MAX_PHOTOS}，第一張是封面；建議順序：搭好全景、內部空間、收納狀態、額外配件、瑕疵特寫）
      </legend>
      <ol className="tents-photo-list">
        {photos.map((sha, index) => (
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
                disabled={busy || index === photos.length - 1}
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
          if (files.length > 0) upload.mutate(files.slice(0, MAX_PHOTOS - photos.length));
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
