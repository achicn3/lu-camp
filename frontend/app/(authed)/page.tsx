// 首頁：模組入口卡片（依完成度開放；操作速度優先，docs/10 §1）。
import Link from "next/link";

const MODULES: { title: string; description: string; href?: string }[] = [
  { title: "POS 結帳", description: "掃碼、購物車、收現找零、列印" },
  { title: "現金對帳", description: "開帳／異動／結帳差異", href: "/cash" },
  { title: "會員/賣方", description: "查詢、建檔、點數、寄售與往來", href: "/contacts" },
  { title: "庫存", description: "序號品、數量品、散裝堆" },
  { title: "收購", description: "買斷／寄售／散裝入庫" },
  { title: "寄售付款", description: "待撥清單、付款、追回提示", href: "/consignment" },
  { title: "裝置狀態", description: "印表機、錢櫃連線" },
];

export default function HomePage() {
  return (
    <section>
      <h1 className="page-title">門市作業</h1>
      <div className="module-grid">
        {MODULES.map((module) =>
          module.href !== undefined ? (
            <Link key={module.title} href={module.href} className="module-card module-card-link">
              <h2>{module.title}</h2>
              <p>{module.description}</p>
            </Link>
          ) : (
            <div key={module.title} className="module-card module-card-disabled">
              <h2>{module.title}</h2>
              <p>{module.description}</p>
              <span className="badge-soon">開發中</span>
            </div>
          ),
        )}
      </div>
    </section>
  );
}
