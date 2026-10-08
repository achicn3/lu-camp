"use client";
// /purchasing 採購／補貨：滿版採購單列表（篩選、搜尋、翻頁）＋建立採購單入口；供應商、進項發票
// 各一個分頁（?tab=invoices 直接開進項發票）。建立／明細／修改採購單與發票各自是獨立頁。
// 全走 OpenAPI 生成型別 client（docs/11）；交易原子性、待收守衛與狀態機都在後端。
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { InputInvoiceTable } from "@/features/purchasing/InputInvoiceTable";
import { LowStockBanner } from "@/features/purchasing/LowStockBanner";
import { PurchaseOrderTable } from "@/features/purchasing/PurchaseOrderTable";
import { SupplierManager } from "@/features/purchasing/SupplierManager";

type Tab = "orders" | "suppliers" | "invoices";

const TABS: { key: Tab; label: string }[] = [
  { key: "orders", label: "採購單" },
  { key: "invoices", label: "進項發票" },
  { key: "suppliers", label: "供應商" },
];

function initialTab(raw: string | null): Tab {
  return TABS.some((t) => t.key === raw) ? (raw as Tab) : "orders";
}

export default function PurchasingPage() {
  // useSearchParams 需要 Suspense 邊界（Next App Router）。
  return (
    <Suspense fallback={<p>載入中…</p>}>
      <Purchasing />
    </Suspense>
  );
}

function Purchasing() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>(() => initialTab(params.get("tab")));

  return (
    <section className="pur-page">
      <div className="pur-page-head">
        <h1 className="page-title">採購 / 補貨</h1>
        {tab === "orders" && (
          <Link href="/purchasing/new" className="btn-primary pur-create-link">
            ＋ 建立採購單
          </Link>
        )}
      </div>

      <div className="settle-tabs" aria-label="採購功能">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`chip ${tab === t.key ? "chip-active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "orders" && (
        <>
          <LowStockBanner />
          <PurchaseOrderTable />
        </>
      )}
      {tab === "invoices" && <InputInvoiceTable />}
      {tab === "suppliers" && <SupplierManager />}
    </section>
  );
}
