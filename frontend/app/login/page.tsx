"use client";
// /login：帳密登入 → 取 JWT（docs/10 §5）。錯誤訊息 inline 呈現（401 帳密錯誤/429 節流）。
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setError(null);
    const result = await login(String(form.get("username")), String(form.get("password")));
    setSubmitting(false);
    if (result.ok) {
      router.replace("/");
      return;
    }
    setError(result.message);
  }

  return (
    <main className="login-screen">
      <form className="login-card" onSubmit={onSubmit}>
        <h1 className="login-title">露營二手 POS</h1>
        <label className="field">
          <span className="field-label">帳號</span>
          <input name="username" autoComplete="username" required autoFocus />
        </label>
        <label className="field">
          <span className="field-label">密碼</span>
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {error !== null && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? "登入中…" : "登入"}
        </button>
      </form>
    </main>
  );
}
