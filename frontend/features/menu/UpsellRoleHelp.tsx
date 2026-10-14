import type { components } from "@/lib/api-types";

type Role = NonNullable<components["schemas"]["MenuPresentationUpdateRequest"]["role"]>;
const EXAMPLES: Record<Role, string> = {
  coffee: "客人點甜點時，這杯咖啡可出現在搭配推薦中；客人點這杯咖啡時，會推薦甜點。",
  dessert: "客人點咖啡時，這份甜點可出現在搭配推薦中；客人點這份甜點時，會推薦咖啡。",
  experience: "客人選這個手沖體驗時，會推薦咖啡豆或濾掛，方便把喜歡的風味帶回家。",
  bean: "客人選手沖體驗或其他咖啡豆時，這款豆子可出現在搭配推薦中；客人選這款豆子時，會推薦濾掛或其他豆款。",
  drip: "客人選手沖體驗或咖啡豆時，這款濾掛可出現在搭配推薦中。",
  other: "目前尚未設定「其他」類型的搭配規則，選這項暫時不會產生搭配推薦。",
};

export function UpsellRoleHelp({ role }: { role: Role | "" }) {
  return <div className="hint">
    <p>請選這個商品本身的類型，例如拿鐵選「咖啡」、蛋糕選「甜點」。系統會用它安排客人購物車下方的搭配推薦。</p>
    <p>{role ? EXAMPLES[role] : "這個商品不參與搭配推薦，客人仍可從菜單正常點選。"}</p>
    <p>最多推薦 2 項可售且尚未加入購物車的商品，由客人自行選購。儲存後請再發佈到線上點餐。</p>
  </div>;
}
