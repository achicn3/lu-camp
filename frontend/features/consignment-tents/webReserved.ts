// POS 掃到寄售帳篷時查官網是否標「已有人保留」（docs/97 裁示 6：提醒不擋）。
// 查不到、網路錯誤一律當作沒保留——這只是提醒，不能因為它擋住結帳。
import { api } from "@/lib/api";

export async function isWebReserved(itemCode: string): Promise<boolean> {
  try {
    const { data } = await api.GET("/api/v1/online-order/tents/by-item-code/{item_code}", {
      params: { path: { item_code: itemCode } },
    });
    return data?.reserved === true;
  } catch {
    return false;
  }
}
