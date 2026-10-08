"use client";
// /menu/retail/[id] 編輯帶回家商品（含照片）。
import { useParams } from "next/navigation";

import { RetailEditor } from "../RetailEditor";

export default function EditRetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  return <RetailEditor listingId={Number.isInteger(id) && id > 0 ? id : -1} />;
}
