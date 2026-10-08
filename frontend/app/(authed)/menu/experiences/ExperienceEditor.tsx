"use client";
// 體驗卡新增／編輯頁的共用外框：載入菜單品項（與既有卡），存好或取消都回「線上發布」分頁。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ExperienceForm } from "@/features/menu/ExperienceForm";
import { apiDetail } from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";

const BACK = "/menu?section=online";

export function ExperienceEditor({ experienceId }: { experienceId: number | null }) {
  const router = useRouter();
  const items = useQuery({
    queryKey: ["menu-items"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-items");
      if (!data) throw new Error(apiDetail(error, "讀取菜單失敗"));
      return data;
    },
  });
  const experiences = useQuery({
    queryKey: ["menu-experiences"],
    enabled: experienceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/experiences");
      if (!data) throw new Error(apiDetail(error, "讀取體驗卡失敗"));
      return data;
    },
  });
  const initial = experienceId === null ? null : experiences.data?.find((row) => row.id === experienceId);
  const loading = items.isPending || (experienceId !== null && experiences.isPending);
  const failed = items.error ?? experiences.error;

  return (
    <section className="exp-page">
      <Link href={BACK} className="pur-back">← 回線上發布</Link>
      <h1 className="page-title">{experienceId === null ? "新增手沖體驗卡" : "編輯手沖體驗卡"}</h1>
      {loading && <p role="status">載入中…</p>}
      {failed && <p role="alert" className="form-error">{failed.message}</p>}
      {!loading && !failed && experienceId !== null && initial === undefined && (
        <p role="alert" className="form-error">找不到這張體驗卡，可能已經刪除了。</p>
      )}
      {!loading && !failed && initial !== undefined && (
        <ExperienceForm
          initial={initial}
          items={items.data ?? []}
          onDone={() => router.push(BACK)}
          onCancel={() => router.push(BACK)}
        />
      )}
    </section>
  );
}
