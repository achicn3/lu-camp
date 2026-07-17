"use client";
// 受保護區殼層：無 token 導回 /login；監聽 401 廣播；頂欄導覽＋身分/登出。
// 前端隱藏不等於安全——後端對每個請求仍驗權（docs/10 §4）。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { decodeSession, logout, readTokenRole } from "@/lib/auth";
import { UNAUTHORIZED_EVENT, getToken, subscribeToken } from "@/lib/token";

// managerOnly：管理專屬頁（後端亦限 MANAGER）——CLERK 導覽收斂，不顯示無權入口
// （前端隱藏非安全邊界，後端每請求仍驗權；此處只為店員介面不顯示點不進去的入口）。
const NAV_ITEMS: { href: string; label: string; ready: boolean; managerOnly?: boolean }[] = [
  { href: "/", label: "首頁", ready: true },
  { href: "/pos", label: "POS 結帳", ready: true },
  { href: "/sales", label: "交易紀錄", ready: true },
  { href: "/signing", label: "簽署紀錄", ready: true },
  { href: "/cash", label: "現金對帳", ready: true },
  { href: "/contacts", label: "會員/賣方", ready: true },
  { href: "/inventory", label: "庫存", ready: true },
  { href: "/acquisition", label: "收購", ready: true },
  { href: "/consignment", label: "寄售付款", ready: true },
  { href: "/purchasing", label: "採購補貨", ready: true },
  { href: "/stocktake", label: "盤點", ready: true },
  { href: "/campaigns", label: "門市活動", ready: true, managerOnly: true },
  { href: "/menu", label: "餐飲菜單", ready: true, managerOnly: true },
  { href: "/reports", label: "報表", ready: true, managerOnly: true },
  { href: "/settings", label: "設定", ready: true, managerOnly: true },
];

const emptySubscribe = () => () => {};

export default function AuthedLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  // token 為外部 store（記憶體＋localStorage）；SSR 快照為 null → 客戶端水合後同步。
  const token = useSyncExternalStore(subscribeToken, getToken, () => null);
  // 水合完成偵測（伺服器快照 false / 客戶端 true；無 setState-in-effect）：
  // 水合首輪 token 必為 null（server 快照），不可在還原 sessionStorage 前就誤導去登入。
  const hydrated = useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );

  useEffect(() => {
    if (!hydrated) return;
    // 無 token，或 token 非有效店務身分（KIOSK 簽署裝置 token 使 decodeSession()→null，
    // 或格式損毀）→ 不得渲染店務殼（否則客人面向的 KIOSK 裝置導到店務頁時，會在後端
    // 403 前短暫看到快取的店務資料）。KIOSK 一律導回其專用 /kiosk（Codex K3 medium）。
    if (token === null || decodeSession() === null) {
      router.replace(readTokenRole() === "KIOSK" ? "/kiosk" : "/login");
    }
  }, [hydrated, token, router]);

  useEffect(() => {
    // 401 即清空 React Query 快取：避免被降權/換人後，仍以前一身分的快取資料/授權結果渲染。
    const onUnauthorized = () => {
      queryClient.clear();
      router.replace("/login");
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [router, queryClient]);

  // 導覽依權限收斂用「DB 現值角色」而非 token claim（永不過期 token 下升/降權未重登會過時；
  // Codex 波次三 P2）。/auth/me 每次以 DB 覆核；載入中先回退 token claim 避免閃爍。
  const me = useQuery({
    queryKey: ["auth-me"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/auth/me");
      return data ?? null;
    },
    enabled: token !== null,
    staleTime: 30_000,
  });

  // 硬性閘門：非店務身分（含 KIOSK token）一律不渲染店務殼與其子頁，杜絕快取資料外洩。
  const session = decodeSession();
  if (!hydrated || token === null || session === null) return null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <nav className="app-nav">
          {NAV_ITEMS.filter(
            // DB 現值角色優先；未載入時回退 token claim（僅影響短暫首幀）
            (item) => !item.managerOnly || (me.data?.role ?? session.role) === "MANAGER",
          ).map((item) =>
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
              // 清空快取，確保下一位登入者不會短暫看到前一位的報表/設定資料或授權結果。
              queryClient.clear();
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
