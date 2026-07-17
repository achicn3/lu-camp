// 目前登入者的「DB 現值角色」——導覽/首頁卡片/頁面 gate 共用（Codex 波次三第二輪）。
// 永不過期 token 下不可信 JWT 的 role claim（升/降權未重登會過時）：/auth/me 每請求由後端
// 以 DB 覆核（D-4）。此 hook：① 非 2xx 時 throw 讓 React Query 重試、不靜默回退；
// ② 每 60 秒輪詢，角色變更於留在受保護頁時也會傳播；③ 載入中回退 token claim 避免閃爍。
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";
import { getToken } from "@/lib/token";

export interface CurrentRole {
  /** DB 現值角色；初次載入中回退 token claim，查詢失敗則 null（fail-closed）。 */
  role: string | null;
  isManager: boolean;
  /** 權威查詢失敗（重試耗盡且從無成功資料）：呼叫端可據此顯示提示。 */
  roleUnavailable: boolean;
}

export function useCurrentRole(): CurrentRole {
  const claimRole = decodeSession()?.role ?? null;
  const query = useQuery({
    queryKey: ["auth-me"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      // openapi-fetch 非 2xx 不會 throw、data 為 undefined → 顯式 throw 讓 React Query 重試，
      // 不靜默回退 stale JWT（Codex 波次三第二輪 P2）。
      if (!data) throw new Error(`auth/me 失敗（HTTP ${response.status}）`);
      return data;
    },
    enabled: getToken() !== null,
    staleTime: 30_000,
    refetchInterval: 60_000, // 角色變更於留在受保護頁時傳播（無重登需求）
  });
  // 權威優先：有 data 即用 DB 現值（背景輪詢失敗時仍保留上次成功值，非 stale JWT）。
  // 僅「初次載入中（從未取得）」回退 token claim 避免閃爍；查詢失敗（重試耗盡、從無資料）
  // 則 fail-closed 回 null——降權者於 /auth/me 中斷期間不得續顯管理 UI（Codex 波次三第三輪 P2）。
  const role = query.data?.role ?? (query.isPending ? claimRole : null);
  const roleUnavailable = query.data === undefined && !query.isPending;
  return { role, isManager: role === "MANAGER", roleUnavailable };
}
