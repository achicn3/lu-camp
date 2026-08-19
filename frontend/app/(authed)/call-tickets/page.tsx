"use client";
// /call-tickets 叫號（docs/38）：收購前的候位清單。
// 客人賣東西前先填表單、把連結傳來，店家登記後取號，處理完按「完成」。
// 不限店長（排隊是日常作業）；不與收購流程串接。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { isSafeExternalLink, ticketLabel } from "@/features/call-tickets/callTickets";

type CallTicket = components["schemas"]["CallTicketRead"];

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

export default function CallTicketsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [link, setLink] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 剛取到的號碼——這個數字是要喊出口的，取號後大大地顯示出來。
  const [justIssued, setJustIssued] = useState<CallTicket | null>(null);

  const tickets = useQuery({
    queryKey: ["call-tickets"],
    queryFn: async () => {
      const { data, error: err } = await api.GET("/api/v1/call-tickets", {});
      if (!data) throw new Error(extractDetail(err) ?? "讀取候位清單失敗");
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.POST("/api/v1/call-tickets", {
        body: {
          name: name.trim(),
          link: link.trim() === "" ? null : link.trim(),
          note: note.trim() === "" ? null : note.trim(),
        },
      });
      if (!data) throw new Error(extractDetail(err) ?? "取號失敗");
      return data;
    },
    onSuccess: (ticket) => {
      setJustIssued(ticket);
      setName("");
      setLink("");
      setNote("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["call-tickets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const complete = useMutation({
    mutationFn: async (id: number) => {
      const { data, error: err } = await api.POST(
        "/api/v1/call-tickets/{ticket_id}/complete",
        { params: { path: { ticket_id: id } } },
      );
      if (!data) throw new Error(extractDetail(err) ?? "標記完成失敗");
      return data;
    },
    onSuccess: (ticket) => {
      setError(null);
      // 完成的那筆若正顯示在「剛取號」區，一併收掉，避免畫面上自相矛盾。
      setJustIssued((prev) => (prev?.id === ticket.id ? null : prev));
      void queryClient.invalidateQueries({ queryKey: ["call-tickets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const rows = tickets.data ?? [];
  const canSubmit = name.trim() !== "" && !create.isPending;

  return (
    <section>
      <h1 className="page-title">叫號</h1>
      <p className="hint">
        客人要賣東西時先在此登記取號；處理完按「完成」。表單連結可留著日後回查。
      </p>

      <div className="card call-ticket-form">
        <label className="field">
          <span className="field-label">稱呼（必填）</span>
          <input
            name="name"
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            placeholder="例：王先生"
          />
        </label>
        <label className="field">
          <span className="field-label">表單連結</span>
          <input
            name="link"
            value={link}
            maxLength={500}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://..."
          />
        </label>
        <label className="field">
          <span className="field-label">備註</span>
          <input
            name="note"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            placeholder="例：帳篷兩頂、桌椅一組"
          />
        </label>
        <button
          type="button"
          className="btn-primary call-ticket-issue"
          disabled={!canSubmit}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "取號中…" : "取號"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}

      {justIssued !== null && (
        <div className="card call-ticket-issued" role="status">
          <p className="hint">請告知客人號碼</p>
          <p className="call-ticket-big-number">{justIssued.ticket_no}</p>
          <p className="hint">{justIssued.name}</p>
        </div>
      )}

      <h2>候位中</h2>
      {tickets.isError && (
        <p role="alert" className="form-error">
          {(tickets.error as Error).message}
        </p>
      )}
      {tickets.isSuccess && rows.length === 0 && <p className="hint">目前沒有人在候位。</p>}
      {rows.length > 0 && (
        <div className="card">
          <table className="data-table call-ticket-list">
            <thead>
              <tr>
                <th>號碼</th>
                <th>稱呼</th>
                <th>表單連結</th>
                <th>備註</th>
                <th>登記時間</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {rows.map((ticket) => (
                <tr key={ticket.id}>
                  {/* 跨日未完成的仍留在清單（客人真的還在等），號碼前加日期以免與今日混淆 */}
                  <td className="call-ticket-no">{ticketLabel(ticket)}</td>
                  <td>{ticket.name}</td>
                  <td>
                    {ticket.link !== null && isSafeExternalLink(ticket.link) ? (
                      // rel 必給：noopener 防被開啟的頁面反向操作本視窗
                      <a href={ticket.link} target="_blank" rel="noopener noreferrer">
                        開啟表單
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{ticket.note ?? "—"}</td>
                  <td>{formatTaipeiDateTime(ticket.created_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`完成叫號 ${ticket.ticket_no}`}
                      disabled={complete.isPending}
                      onClick={() => complete.mutate(ticket.id)}
                    >
                      完成
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
