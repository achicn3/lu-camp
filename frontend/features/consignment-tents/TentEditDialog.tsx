"use client";
// 編輯一頂寄售帳篷的官網資料（docs/97 §6）。照片上傳／排序／刪除當下就存；其他欄位按「儲存」才送。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import {
  bodyFrom,
  draftFrom,
  ListingFields,
  ListingToggles,
} from "@/features/consignment-tents/ListingFields";
import {
  extractDetail,
  photoForm,
  type PhotoOps,
  TentPhotos,
} from "@/features/consignment-tents/TentPhotos";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type Tent = components["schemas"]["TentListingRead"];

function itemPhotoOps(itemId: number): PhotoOps {
  const path = { item_id: itemId };
  return {
    upload: async (file) => {
      const { data, error } = await api.POST("/api/v1/online-order/tents/{item_id}/photos", {
        params: { path },
        ...photoForm(file),
      });
      if (!data) throw new Error(extractDetail(error) ?? "上傳照片失敗");
      return data.photos;
    },
    reorder: async (photos) => {
      const { data, error } = await api.PUT(
        "/api/v1/online-order/tents/{item_id}/photos/order",
        { params: { path }, body: { photos } },
      );
      if (!data) throw new Error(extractDetail(error) ?? "調整順序失敗");
      return data.photos;
    },
    remove: async (sha256) => {
      const { data, error } = await api.DELETE(
        "/api/v1/online-order/tents/{item_id}/photos/{sha256}",
        { params: { path: { ...path, sha256 } } },
      );
      if (!data) throw new Error(extractDetail(error) ?? "刪除照片失敗");
      return data.photos;
    },
  };
}

export function TentEditDialog({
  tent,
  onClose,
  onSaved,
  onPhotosChanged,
}: {
  tent: Tent;
  onClose: () => void;
  onSaved: () => void;
  onPhotosChanged: () => void;
}) {
  const [photos, setPhotos] = useState(tent.photos);
  const [draft, setDraft] = useState(() => draftFrom(tent));
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.PUT("/api/v1/online-order/tents/{item_id}", {
        params: { path: { item_id: tent.item_id } },
        body: bodyFrom(draft),
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

        <ListingToggles draft={draft} onChange={setDraft} />
        <ListingFields draft={draft} onChange={setDraft} />

        <TentPhotos
          photos={photos}
          ops={itemPhotoOps(tent.item_id)}
          onChange={(next) => {
            setPhotos(next);
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
