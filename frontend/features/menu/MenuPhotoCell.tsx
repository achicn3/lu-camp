"use client";
// 菜單品項的照片欄（docs/44 §3.4；O1d）：縮圖＋上傳／換照片＋移除。
// 轉檔（WebP、縮圖、去掉 GPS）全在後端做；這裡只先擋超過 10 MB 的檔，省得白傳。
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { MAX_PHOTO_BYTES, menuPhotoUrl } from "@/features/menu/menuPhoto";
import { api } from "@/lib/api";

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export function MenuPhotoCell({
  item,
  onChanged,
}: {
  item: { id: number; name: string; photo_sha256?: string | null };
  onChanged: () => void;
}) {
  const inputId = useId();
  const [error, setError] = useState<string | null>(null);
  const hasPhoto = typeof item.photo_sha256 === "string";

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const { data, error: err } = await api.POST("/api/v1/menu-items/{item_id}/photo", {
        params: { path: { item_id: item.id } },
        // 型別上 file 是 string（OpenAPI 的 binary）；實際送 multipart，由 bodySerializer 組 FormData。
        body: { file: "" },
        bodySerializer: () => {
          const form = new FormData();
          form.append("file", file, file.name);
          return form;
        },
      });
      if (!data) throw new Error(extractDetail(err) ?? "上傳照片失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.DELETE("/api/v1/menu-items/{item_id}/photo", {
        params: { path: { item_id: item.id } },
      });
      if (!data) throw new Error(extractDetail(err) ?? "移除照片失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err: Error) => setError(err.message),
  });

  function pick(file: File | undefined) {
    if (file === undefined) return;
    if (file.size > MAX_PHOTO_BYTES) {
      setError("照片超過 10 MB，請先縮小再上傳");
      return;
    }
    setError(null);
    upload.mutate(file);
  }

  const busy = upload.isPending || remove.isPending;
  return (
    <div className="menu-photo-cell">
      {hasPhoto ? (
        // eslint-disable-next-line @next/next/no-img-element -- 後端已轉好 WebP，不經 next/image 最佳化
        <img
          className="menu-photo-thumb"
          src={menuPhotoUrl(item.photo_sha256 as string)}
          alt={`${item.name} 照片`}
          loading="lazy"
        />
      ) : (
        <span className="menu-photo-empty" aria-hidden="true" />
      )}
      <div className="menu-photo-actions">
        <label
          htmlFor={inputId}
          className={`btn-ghost menu-photo-upload${busy ? " is-disabled" : ""}`}
        >
          {upload.isPending ? "上傳中…" : hasPhoto ? "換照片" : "上傳照片"}
        </label>
        <input
          id={inputId}
          className="sr-only"
          type="file"
          accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
          aria-label={`${item.name} 上傳照片`}
          disabled={busy}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = ""; // 同一個檔再選一次也要觸發
          }}
        />
        {hasPhoto && (
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            aria-label={`${item.name} 移除照片`}
            disabled={busy}
            onClick={() => remove.mutate()}
          >
            移除
          </button>
        )}
      </div>
      {error !== null && (
        <p role="alert" className="form-error menu-row-error">
          {error}
        </p>
      )}
    </div>
  );
}
