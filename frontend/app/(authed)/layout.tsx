"use client";
// 受保護區殼層：無 token 導回 /login；監聽 401 廣播；頂欄導覽＋身分/登出。
// 前端隱藏不等於安全——後端對每個請求仍驗權（docs/10 §4）。
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useSyncExternalStore } from "react";

import { decodeSession, logout } from "@/lib/auth";
import { UNAUTHORIZED_EVENT, getToken, subscribeToken } from "@/lib/token";

const NAV_ITEMS: { href: string; label: string; ready: boolean }[] = [
  { href: "/", label: "首頁", ready: true },
  { href: "/pos", label: "POS 結帳", ready: true },
  { href: "/cash", label: "現金對帳", ready: true },
  { href: "/contacts", label: "會員/賣方", ready: false },
  { href: "/inventory", label: "庫存", ready: false },
  { href: "/acquisition", label: "收購", ready: false },
];

const emptySubscribe = () => () => {};

export default function AuthedLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  // token 為外部 store（記憶體＋sessionStorage）；SSR 快照為 null → 客戶端水合後同步。
  const token = useSyncExternalStore(subscribeToken, getToken, () => null);
  // 水合完成偵測（伺服器快照 false / 客戶端 true；無 setState-in-effect）：
  // 水合首輪 token 必為 null（server 快照），不可在還原 sessionStorage 前就誤導去登入。
  const hydrated = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );

  useEffect(() => {
    if (hydrated && token === null) router.replace("/login");
  }, [hydrated, token, router]);

  useEffect(() => {
    const onUnauthorized = () => router.replace("/login");
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [router]);

  if (!hydrated || token === null) return null;
  const session = decodeSession();

  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-nav">
          {NAV_ITEMS.map((item) =>
            item.ready ? (
              <Link key={item.href} href={item.href} className="nav-link">
                {item.label}
              </Link>
            ) : (
              <span key={item.href} className="nav-link nav-link-disabled" title="開發中">
                {item.label}
              </span>
            ),
          )}
        </nav>
        <div className="app-header-right">
          {session !== null && (
            <span className="session-badge">
              {session.role === "MANAGER" ? "管理者" : "店員"}
            </span>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            登出
          </button>
        </div>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
