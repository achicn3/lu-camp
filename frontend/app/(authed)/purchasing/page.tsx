"use client";
// /purchasing 採購／補貨：滿版採購單列表（篩選、搜尋、翻頁）＋建立採購單入口；供應商另一個分頁。
// 建立採購單與採購單明細各自是獨立頁（/purchasing/new、/purchasing/[id]）。
// 全走 OpenAPI 生成型別 client（docs/11）；交易原子性、待收守衛與狀態機都在後端。
import Link from "next/link";
import { useState } from "react";

import { LowStockBanner } from "@/features/purchasing/LowStockBanner";
import { PurchaseOrderTable } from "@/features/purchasing/PurchaseOrderTable";
import { SupplierManager } from "@/features/purchasing/SupplierManager";

type Tab = "orders" | "suppliers";

export default function PurchasingPage() {
  const [tab, setTab] = useState<Tab>("orders");

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
        <button
          type="button"
          className={`chip ${tab === "orders" ? "chip-active" : ""}`}
          onClick={() => setTab("orders")}
        >
          採購單
        </button>
        <button
          type="button"
          className={`chip ${tab === "suppliers" ? "chip-active" : ""}`}
          onClick={() => setTab("suppliers")}
        >
          供應商
        </button>
      </div>

      {tab === "orders" ? (
        <>
          <LowStockBanner />
          <PurchaseOrderTable />
        </>
      ) : (
        <SupplierManager />
      )}
    </section>
  );
}
