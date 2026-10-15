"use client";
// /menu/table-qr 桌號 QR 一鍵列印（店主 2026-10-15）：?table=A1 只先勾那一桌（重發 QR 後重印用）。
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { TableQrSheet } from "@/features/menu/TableQrSheet";

const BACK = "/menu?section=online";

function Sheet() {
  const table = useSearchParams().get("table");
  return <TableQrSheet initial={table ?? undefined} />;
}

export default function TableQrPage() {
  return (
    <section className="exp-page table-qr-page">
      <Link href={BACK} className="pur-back">← 回線上發布</Link>
      <h1 className="page-title">列印桌號 QR</h1>
      <Suspense fallback={<p>載入中…</p>}>
        <Sheet />
      </Suspense>
    </section>
  );
}
