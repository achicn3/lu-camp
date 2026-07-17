// 目前登入者的「DB 現值角色」——導覽/首頁卡片/頁面 gate 共用（Codex 波次三第二輪）。
// 永不過期 token 下不可信 JWT 的 role claim（升/降權未重登會過時）：/auth/me 每請求由後端
// 以 DB 覆核（D-4）。此 hook：① 非 2xx 時 throw 讓 React Query 重試、不靜默回退；
// ② 每 60 秒輪詢，角色變更於留在受保護頁時也會傳播；③ 載入中回退 token claim 避免閃爍。
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { decodeSession } from "@/lib/auth";
import { getToken } from "@/lib/token";

export interface CurrentRole {
  /** DB 現值角色（載入中回退 token claim）。 */
  role: string | null;
  isManager: boolean;
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
  const role = query.data?.role ?? claimRole;
  return { role, isManager: role === "MANAGER" };
}
