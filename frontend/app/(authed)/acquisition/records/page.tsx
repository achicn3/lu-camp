"use client";
// /acquisition/records 收購紀錄：過去每一張收購單，可篩選、翻頁；管理者可在列上直接作廢。
import Link from "next/link";

import { AcquisitionRecords } from "@/features/acquisition/AcquisitionRecords";

export default function AcquisitionRecordsPage() {
  return (
    <section className="acq-records-page">
      <div className="pur-page-head">
        <h1 className="page-title">收購紀錄</h1>
        <Link href="/acquisition" className="btn-primary">
          ＋ 新增收購
        </Link>
      </div>
      <AcquisitionRecords />
    </section>
  );
}
