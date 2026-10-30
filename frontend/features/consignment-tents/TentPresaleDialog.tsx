"use client";
// 新增／編輯預售（docs/102）：帳篷還在賣家家裡就先上官網。店員照 Google 表單抄進來；
// 賣家姓名電話只在 POS。照片要先建立預售才能加（照片掛在這筆預售上）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { CreatableCombobox } from "@/features/acquisition/CreatableCombobox";
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
import { parseNtd } from "@/lib/money";

type Presale = components["schemas"]["TentPresaleRead"];

function presalePhotoOps(listingId: number): PhotoOps {
  const path = { listing_id: listingId };
  return {
    upload: async (file) => {
      const { data, error } = await api.POST(
        "/api/v1/online-order/tents/presales/{listing_id}/photos",
        { params: { path }, ...photoForm(file) },
      );
      if (!data) throw new Error(extractDetail(error) ?? "上傳照片失敗");
      return data.photos;
    },
    reorder: async (photos) => {
      const { data, error } = await api.PUT(
        "/api/v1/online-order/tents/presales/{listing_id}/photos/order",
        { params: { path }, body: { photos } },
      );
      if (!data) throw new Error(extractDetail(error) ?? "調整順序失敗");
      return data.photos;
    },
    remove: async (sha256) => {
      const { data, error } = await api.DELETE(
        "/api/v1/online-order/tents/presales/{listing_id}/photos/{sha256}",
        { params: { path: { ...path, sha256 } } },
      );
      if (!data) throw new Error(extractDetail(error) ?? "刪除照片失敗");
      return data.photos;
    },
  };
}

async function searchBrands(q: string) {
  const { data } = await api.GET("/api/v1/brands", { params: { query: { q } } });
  return data ?? [];
}

async function createBrand(name: string) {
  const { data, error } = await api.POST("/api/v1/brands", { body: { name } });
  if (!data) throw new Error(extractDetail(error) ?? "新增品牌失敗");
  return data;
}

export function TentPresaleDialog({
  presale: initial,
  onClose,
  onSaved,
  onChanged,
}: {
  /** null＝新增。 */
  presale: Presale | null;
  onClose: () => void;
  /** 按「儲存」成功（關掉視窗、推上官網）。 */
  onSaved: () => void;
  /** 建立或照片有變動（清單要重抓，視窗不關）。 */
  onChanged: () => void;
}) {
  const [presale, setPresale] = useState(initial);
  const [brand, setBrand] = useState<{ id: number; name: string } | null>(
    initial ? { id: initial.brand_id, name: initial.brand_name ?? "" } : null,
  );
  const [name, setName] = useState(initial?.name ?? "");
  const [price, setPrice] = useState(initial?.price ?? "");
  const [sellerName, setSellerName] = useState(initial?.seller_name ?? "");
  const [sellerPhone, setSellerPhone] = useState(initial?.seller_phone ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [draft, setDraft] = useState(() => draftFrom(initial));
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      if (brand === null) throw new Error("請選品牌");
      const parsedPrice = parseNtd(price);
      if (parsedPrice === null || parsedPrice <= 0) throw new Error("售價請填大於 0 的整數");
      const body = {
        ...bodyFrom(draft),
        brand_id: brand.id,
        name,
        price: String(parsedPrice),
        seller_name: sellerName,
        seller_phone: sellerPhone,
        note: note.trim() === "" ? null : note,
      };
      const { data, error: err } =
        presale === null
          ? await api.POST("/api/v1/online-order/tents/presales", { body })
          : await api.PUT("/api/v1/online-order/tents/presales/{listing_id}", {
              params: { path: { listing_id: presale.listing_id } },
              body,
            });
      if (!data) throw new Error(extractDetail(err) ?? "儲存失敗");
      return { data, created: presale === null };
    },
    onSuccess: ({ data, created }) => {
      if (created) {
        // 剛建立：視窗留著讓店員接著加照片
        setPresale(data);
        onChanged();
        return;
      }
      onSaved();
    },
    onError: (err: Error) => setError(err.message),
  });

  const title = presale === null ? "新增預售" : `預售 ${presale.presale_code}`;
  return (
    <div className="pos-dialog-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <form
        className="card pos-dialog tents-dialog"
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          save.mutate();
        }}
      >
        <h2>
          {presale === null ? "新增預售" : [brand?.name, name].filter(Boolean).join(" ")}
          {presale !== null && <span className="tents-dialog-code">{presale.presale_code}</span>}
        </h2>
        <p className="hint">
          帳篷還在賣家家裡，先放上官網（顯示「預售」、想看要先預約）。帳篷送到店時，在收購頁建寄售單、選「從預售帶入」。
        </p>

        <div className="tents-grid">
          <CreatableCombobox
            label="品牌"
            placeholder="選擇或新增品牌"
            selectedId={brand?.id ?? null}
            selectedName={brand?.name ?? null}
            search={searchBrands}
            create={createBrand}
            onChange={(o) => setBrand(o)}
          />
          <label className="field">
            <span className="field-label">帳篷名稱</span>
            <input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">售價（客人實付，到店後可在收購頁改）</span>
            <input inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value)} />
          </label>
        </div>
        <div className="tents-grid">
          <label className="field">
            <span className="field-label">賣家姓名（只在 POS）</span>
            <input
              value={sellerName}
              maxLength={50}
              onChange={(e) => setSellerName(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">賣家電話（只在 POS）</span>
            <input
              inputMode="tel"
              value={sellerPhone}
              maxLength={30}
              onChange={(e) => setSellerPhone(e.target.value)}
            />
          </label>
        </div>
        <label className="field tents-textarea">
          <span className="field-label">備註（只給店員看，例如什麼時候方便帶來）</span>
          <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>

        <ListingToggles draft={draft} onChange={setDraft} />
        <ListingFields draft={draft} onChange={setDraft} />

        {presale === null ? (
          <p className="hint">按「建立預售」後就可以加照片。</p>
        ) : (
          <TentPhotos
            photos={presale.photos}
            ops={presalePhotoOps(presale.listing_id)}
            onChange={(photos) => {
              setPresale({ ...presale, photos });
              onChanged();
            }}
          />
        )}

        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="pos-dialog-actions">
          <button type="submit" className="btn-primary" disabled={save.isPending}>
            {save.isPending ? "儲存中…" : presale === null ? "建立預售" : "儲存"}
          </button>
          <button type="button" className="btn-ghost" onClick={presale === null ? onClose : onSaved}>
            {presale === null ? "取消" : "關閉"}
          </button>
        </div>
      </form>
    </div>
  );
}
