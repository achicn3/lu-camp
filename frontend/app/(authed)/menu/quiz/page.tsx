"use client";
// /menu/quiz 「不知道喝什麼」引導推薦設定（docs/63 §2 M2a）：載入題目、菜單品項與體驗卡，存好或取消回「線上發布」。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { apiDetail } from "@/features/menu/experienceOptions";
import { QuizForm } from "@/features/menu/QuizForm";
import { api } from "@/lib/api";

const BACK = "/menu?section=online";

export default function MenuQuizPage() {
  const router = useRouter();
  const quiz = useQuery({
    queryKey: ["menu-quiz"],
    // 一定要拿最新的一份才帶入表單（快取會帶出別處改之前的內容）。
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/quiz");
      if (!data) throw new Error(apiDetail(error, "讀取引導推薦失敗"));
      return data;
    },
  });
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
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/experiences");
      if (!data) throw new Error(apiDetail(error, "讀取體驗卡失敗"));
      return data;
    },
  });
  const loading =
    quiz.isPending ||
    (!quiz.isFetchedAfterMount && quiz.isFetching) ||
    items.isPending ||
    experiences.isPending;
  const failed = quiz.error ?? items.error ?? experiences.error;

  return (
    <section className="exp-page">
      <Link href={BACK} className="pur-back">← 回線上發布</Link>
      <h1 className="page-title">不知道喝什麼？（引導推薦）</h1>
      <p className="hint">
        每個答案勾「適合的品項」。客人答完，被勾到最多次的最推薦，其次兩個當備選；售完或下架的會自動略過。
      </p>
      {loading && <p role="status">載入中…</p>}
      {failed && <p role="alert" className="form-error">{failed.message}</p>}
      {!loading && !failed && quiz.data && (
        <QuizForm
          initial={quiz.data}
          items={items.data ?? []}
          experiences={experiences.data ?? []}
          onDone={() => router.push(BACK)}
          onCancel={() => router.push(BACK)}
        />
      )}
    </section>
  );
}
