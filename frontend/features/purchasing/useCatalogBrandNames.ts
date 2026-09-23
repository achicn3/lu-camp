// 一般商品的品牌名稱對照。來源是「實際有一般商品掛著的品牌」（不設上限），
// 不用 /brands 清單——那支有 200 筆上限，品牌一多，名單外的商品就永遠查不到名稱、印不了標籤。
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * 回傳 id → 品牌名：沒有品牌回 null；還沒載入或查不到回 undefined
 * （印標籤看到 undefined 會停用，不會把「查不到」當成「沒有品牌」印出去）。
 */
export function useCatalogBrandNames(): (id: number | null) => string | null | undefined {
  const brands = useQuery({
    queryKey: ["catalog-products", "filter-options"],
    queryFn: async () =>
      (await api.GET("/api/v1/catalog-products/filter-options")).data?.brands ?? [],
  });
  return (id) => {
    if (id === null) return null;
    return (brands.data ?? []).find((brand) => brand.id === id)?.name;
  };
}
