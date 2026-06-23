// 首頁：模組入口卡片（與導覽列同步；皆已上線可點，操作速度優先，docs/10 §1）。
import Link from "next/link";

const MODULES: { title: string; description: string; href: string }[] = [
  { title: "POS 結帳", description: "掃碼、購物車、餐飲點餐、收現找零、列印", href: "/pos" },
  { title: "現金對帳", description: "開帳／異動／結帳差異", href: "/cash" },
  { title: "會員/賣方", description: "查詢、建檔、點數、寄售與往來", href: "/contacts" },
  { title: "庫存", description: "序號品、數量品、散裝堆、補印標籤", href: "/inventory" },
  { title: "收購", description: "買斷／寄售／散裝入庫、定價輔助", href: "/acquisition" },
  { title: "寄售付款", description: "待撥清單、付款、追回提示", href: "/consignment" },
  { title: "採購補貨", description: "供應商、採購單、收貨入庫、上架數量型商品", href: "/purchasing" },
  { title: "盤點", description: "建立盤點單、輸入實點、確認校正", href: "/stocktake" },
  { title: "門市活動", description: "限時促銷折扣建立與成效", href: "/campaigns" },
  { title: "餐飲菜單", description: "內用品項上下架、改價（MANAGER）", href: "/menu" },
  { title: "報表", description: "今日營運、趨勢、現金、毛利、庫存、寄售", href: "/reports" },
  { title: "設定", description: "稅率、抽成、購物金溢價與低消門檻", href: "/settings" },
];

export default function HomePage() {
  return (
    <section>
      <h1 className="page-title">門市作業</h1>
      <div className="module-grid">
        {MODULES.map((module) => (
          <Link key={module.title} href={module.href} className="module-card module-card-link">
            <h2>{module.title}</h2>
            <p>{module.description}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
