"use client";
// 「不知道喝什麼」引導推薦的摘要（菜單 →「線上發布」分頁；docs/63 §2 M2a）。設定在獨立頁 /menu/quiz。
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { apiDetail } from "@/features/menu/experienceOptions";
import { api } from "@/lib/api";

export function QuizSection() {
  const quiz = useQuery({
    queryKey: ["menu-quiz"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/online-order/quiz");
      if (!data) throw new Error(apiDetail(error, "讀取引導推薦失敗"));
      return data;
    },
  });
  const data = quiz.data;
  const ticked = new Set(
    (data?.questions ?? []).flatMap((q) => q.options.flatMap((o) => (o.items ?? []).map((r) => `${r.kind}:${r.id}`))),
  ).size;
  const summary =
    data === undefined
      ? null
      : data.is_default
        ? "還沒設定：會先用一版預設題目，勾好品項、打開顯示才會出現在客人頁。"
        : `${data.is_active ? "顯示中" : "未顯示"}・${data.questions.length} 題・勾了 ${ticked} 個品項`;

  return (
    <section className="card menu-experiences" aria-labelledby="menu-quiz-title">
      <div className="menu-experiences-head">
        <div>
          <h2 id="menu-quiz-title">不知道喝什麼？（引導推薦）</h2>
          <p className="hint">客人回答 1–3 題，系統從你勾的品項挑一個最推薦、再給兩個備選。改完到上方按「發佈到線上點餐」。</p>
        </div>
        <Link href="/menu/quiz" className="btn-primary">設定問答</Link>
      </div>
      {quiz.isError && <p role="alert" className="form-error">{quiz.error.message}</p>}
      {summary !== null && <p className="hint">{summary}</p>}
    </section>
  );
}
