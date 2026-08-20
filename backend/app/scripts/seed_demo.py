"""模擬營運資料（docs/37 P0）：12 個月、含季節性與週間分佈的完整營運樣貌。

**非 migration、勿在正式環境執行。** 取代 `seed_dev_demo.py`（後者仍保留供
`inventory-price-smoke` 使用，見 docs/37 §7.1）。

目的：讓儀表板、報表、分頁、篩選、庫存週轉與現金結算等畫面在截圖時皆為有意義的內容，
同時作為效能觀察樣本。

作法（docs/37 §7.2 混合策略）：
- **黃金路徑走 domain service**：確保狀態機、庫存流轉、現金抽屜、寄售結算等不變條件
  被真正觸發，而不是繞過去直接寫表
- 時間序以「建立後回填 `created_at`」鋪出（報表一律以 `Sale.created_at` 篩選）
- 完成後跑**不變條件驗證**（§7.5）；**驗證失敗即視為 seed 失敗**，不得帶著髒資料
  進入截圖階段

執行：

    cd backend
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_demo --seed 42
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_demo --purge
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

# **註冊模型到 metadata**：service 內部組 relationship/FK 時需要對端模型已載入。
# 只 import 直接用到的模型不夠——例如 acquisitions 有指向 signature_tasks 的外鍵，
# 漏掉就會在建立收購時炸 NoReferencedTableError（同 alembic/env.py 與 conftest 的做法）。
import app.modules.backup.models
import app.modules.callticket.models
import app.modules.campaigns.models
import app.modules.cashdrawer.models
import app.modules.consignment.models
import app.modules.customerdisplay.models
import app.modules.einvoice.models
import app.modules.inventory.models
import app.modules.menu.models
import app.modules.purchasing.models
import app.modules.returns.models
import app.modules.sales.models
import app.modules.settings.models
import app.modules.signing.models
import app.modules.stocktake.models
import app.modules.storecredit.models  # noqa: F401
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.national_id import _LETTER_VALUES, _WEIGHTS, is_valid_national_id
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.repository import ContactRepository
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import ContactService
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import AcquisitionType, ContactRole, Grade, PayoutMethod
from app.shared.exceptions import DuplicateContact

_ALLOWED_ENVS = {"development", "test"}
# **資料庫允許清單**（docs/37 §7.9）：硬綁單一名稱會讓它在 e2e 庫上拒跑；
# 但也不能不擋——這支腳本會灌入上萬筆資料，跑錯庫就是災難。
_ALLOWED_DB_NAMES = {"lucamp_manual", "lucamp_e2e"}


# ── 假資料規範（docs/37 §7.8）────────────────────────────────
# 姓名用固定合成名單、電話用**未配發號段**、信箱一律 @example.com、地址虛構。
_SURNAMES = "陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高"
_GIVEN = (
    "志明 淑芬 家豪 雅婷 俊傑 怡君 建宏 美玲 冠廷 詩涵 承翰 佳蓉 宗翰 郁婷 彥廷 "
    "宜蓁 柏翰 欣怡 品豪 筱涵 昱翔 婉婷 冠宇 思穎 育誠 佩君 峻瑋 曉雯 泓瑋 于萱"
).split()
_STREETS = "營區 山線 溪畔 林蔭 星空 曠野 露光 帳篷 野炊 湖畔"


def _fake_name(rng: random.Random) -> str:
    return rng.choice(_SURNAMES) + rng.choice(_GIVEN)


def _fake_phone(serial: int) -> str:
    """未配發號段 09xx——**不可用真實號段**，避免簡訊/來電打到真人。"""
    return f"0900{serial:06d}"


def _fake_address(rng: random.Random) -> str:
    return f"南投縣仁愛鄉{rng.choice(_STREETS)}路{rng.randint(1, 300)}號"


def _make_national_id(rng: random.Random) -> str:
    """產生**檢核碼合法**的身分證字號（店主裁示 2026-08-19）。

    原訂刻意產生錯誤檢核碼，但收購對象必須有 national_id、而建檔 API 拒絕
    檢核碼錯誤的值，兩者無解——裁示以「全程走 service」為優先。
    殘留風險（號碼對應真實格式）已記於 docs/37 §7.8。
    """
    letter = rng.choice(sorted(_LETTER_VALUES))
    gender = rng.choice("12")
    body = "".join(str(rng.randint(0, 9)) for _ in range(7))
    value = _LETTER_VALUES[letter]
    numbers = [value // 10, value % 10, int(gender), *(int(c) for c in body)]
    partial = sum(n * w for n, w in zip(numbers, _WEIGHTS[:-1], strict=True))
    check = (10 - partial % 10) % 10
    candidate = f"{letter}{gender}{body}{check}"
    if not is_valid_national_id(candidate):  # pragma: no cover - 演算法自洽時不會發生
        raise SeedFailed(f"產生的身分證字號檢核碼不合法：{candidate}")
    return candidate


class SeedFailed(SystemExit):
    """seed 失敗（含不變條件驗證未過）。用 SystemExit 讓 CLI 直接非零離開。"""


@dataclass
class InvariantResult:
    """一條不變條件的檢查結果。"""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class SeedReport:
    """seed 的產出摘要與驗證結果，供 `seed_verification.txt` 落檔。"""

    counts: dict[str, int] = field(default_factory=dict)
    invariants: list[InvariantResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i.passed for i in self.invariants)

    def render(self) -> str:
        lines = ["# seed_demo 驗證報告", "", "## 產出數量"]
        lines += [f"- {k}：{v:,}" for k, v in sorted(self.counts.items())]
        lines += ["", "## 不變條件（CLAUDE.md §7）"]
        for inv in self.invariants:
            mark = "✅" if inv.passed else "❌"
            lines.append(f"- {mark} {inv.name}{f'：{inv.detail}' if inv.detail else ''}")
        lines += ["", f"結果：{'全部通過' if self.ok else '**有未通過項目，seed 視為失敗**'}"]
        return "\n".join(lines)


def _guard_environment(database_url: str) -> None:
    """三道環境保護：APP_ENV、明確 opt-in、資料庫名允許清單。

    第三道最重要：前兩道擋不住「APP_ENV 是 development 但 DATABASE_URL 指向
    開發主庫 lucamp」——那正是複審在 docs/37 抓到的 critical 情境。
    """
    settings = get_settings()
    if settings.app_env not in _ALLOWED_ENVS:
        raise SeedFailed(f"拒絕執行：APP_ENV={settings.app_env}（僅 development/test）")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SeedFailed("需 ALLOW_DEV_SEED=true 明確 opt-in")
    db_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name not in _ALLOWED_DB_NAMES:
        raise SeedFailed(
            f"拒絕執行：資料庫 `{db_name}` 不在允許清單 {sorted(_ALLOWED_DB_NAMES)}。"
            "本腳本會灌入上萬筆資料，跑錯庫就是災難。"
        )


async def _require_prerequisites(session: AsyncSession) -> tuple[int, int]:
    """需要先跑過 seed_dev_store 與 seed_dev_user。回傳 (store_id, manager_id)。"""
    store = await session.scalar(select(Store).where(Store.id == 1))
    manager = await session.scalar(select(User).where(User.username == "dev-manager"))
    if store is None or manager is None:
        raise SeedFailed("請先執行 seed_dev_store 與 seed_dev_user")
    return store.id, manager.id


async def _count(session: AsyncSession, table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    value = await session.scalar(text(f"SELECT count(*) FROM {table}{clause}"))
    return int(value or 0)


async def verify_invariants(session: AsyncSession, store_id: int) -> list[InvariantResult]:
    """CLAUDE.md §7 的領域核心不變量（docs/37 §7.5）。

    **這些檢查在 seed 尚未產生對應資料時會是空集合驗證**——會通過但什麼都沒驗到。
    每條都回報實際檢查到的筆數，讓「通過」與「沒東西可驗」在報告上分得出來，
    不要讓綠燈騙人。
    """
    results: list[InvariantResult] = []

    # 1. 序號商品一旦 SOLD 不可再被售出（同一 serialized_item 不得出現在兩張未作廢的單）
    dup = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT l.serialized_item_id FROM sale_lines l"
            "  JOIN sales s ON s.id = l.sale_id"
            "  WHERE l.store_id = :sid AND l.serialized_item_id IS NOT NULL"
            "    AND s.status <> 'VOIDED'"
            "  GROUP BY l.serialized_item_id HAVING count(*) > 1"
            ") t"
        ),
        {"sid": store_id},
    )
    sold = await _count(
        session,
        "sale_lines l JOIN sales s ON s.id = l.sale_id",
        f"l.store_id = {store_id} AND l.serialized_item_id IS NOT NULL AND s.status <> 'VOIDED'",
    )
    results.append(
        InvariantResult("序號商品不重複售出", int(dup or 0) == 0, f"檢查 {sold} 筆序號銷售行")
    )

    # 11. 每筆銷售的明細金額加總 ＝ 交易總額
    mismatch = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT s.id FROM sales s JOIN sale_lines l ON l.sale_id = s.id"
            "  WHERE s.store_id = :sid AND s.status <> 'VOIDED'"
            "  GROUP BY s.id, s.total HAVING sum(l.net_amount) <> s.total"
            ") t"
        ),
        {"sid": store_id},
    )
    sales_n = await _count(session, "sales", f"store_id = {store_id} AND status <> 'VOIDED'")
    results.append(
        InvariantResult("明細加總＝交易總額", int(mismatch or 0) == 0, f"檢查 {sales_n} 筆銷售")
    )

    # 6. 散裝批 remaining_qty 不得 < 0
    negative = await _count(session, "bulk_lots", f"store_id = {store_id} AND remaining_qty < 0")
    lots = await _count(session, "bulk_lots", f"store_id = {store_id}")
    results.append(
        InvariantResult("散裝批餘量不為負", negative == 0, f"檢查 {lots} 堆")
    )

    # 10. 購物金餘額 ＝ 異動流水加總
    # 餘額在獨立的 `store_credit_accounts.balance`（不在 contacts）；
    # 流水金額欄是 `signed_amount`（帶正負號），不是 `amount`。
    drift = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT a.id FROM store_credit_accounts a"
            "  LEFT JOIN store_credit_ledger g ON g.contact_id = a.contact_id"
            "    AND g.store_id = a.store_id"
            "  WHERE a.store_id = :sid"
            "  GROUP BY a.id, a.balance"
            "  HAVING COALESCE(sum(g.signed_amount), 0) <> a.balance"
            ") t"
        ),
        {"sid": store_id},
    )
    accounts_n = await _count(session, "store_credit_accounts", f"store_id = {store_id}")
    results.append(
        InvariantResult("購物金餘額＝流水加總", int(drift or 0) == 0, f"檢查 {accounts_n} 個帳戶")
    )

    return results


# 會員消費分佈（docs/37 §7.7）：約 60% 單次、30% 偶發、10% 常客。
# 這裡只決定「角色與規模」，實際消費次數在銷售階段依此分佈抽樣。
_MEMBER_COUNT = 2200
_SELLER_COUNT = 850


async def seed_contacts(
    session: AsyncSession, store_id: int, rng: random.Random
) -> dict[str, list[int]]:
    """建立會員與賣方／寄售主（相依鏈第 1 步，docs/37 §7.2.1）。

    **全程走 `ContactService.create_contact`**（店主裁示）：手機唯一性、身分證檢核碼、
    加密與盲索引去重都由 service 負責，不繞過。

    回傳 {"members": [...], "sellers": [...]} 供後續階段使用——收購只能掛在
    **有 national_id 的** 賣方/寄售主身上（`AcquisitionRequiresNationalId`）。
    """
    svc = ContactService(session)
    repo = ContactRepository(session)
    members: list[int] = []
    sellers: list[int] = []
    serial = 0

    async def _ensure(payload: ContactCreate) -> int:
        """建檔；已存在（重跑）則**沿用既有那筆**。

        手機以流水號產生，所以重跑必然全部撞號。早期版本在此 `continue`，
        結果是安靜地回傳**空清單**，直到收購階段才炸（`Cannot choose from an
        empty sequence`）——把「已經有資料」誤報成「無法產生資料」。
        """
        try:
            return (await svc.create_contact(store_id, payload)).id
        except DuplicateContact:
            existing = await repo.get_by_phone(store_id, payload.phone)
            if existing is None:  # pragma: no cover - 撞號必找得到
                raise SeedFailed(f"手機 {payload.phone} 撞號卻查不到既有聯絡人") from None
            return existing.id

    for _ in range(_MEMBER_COUNT):
        serial += 1
        members.append(
            await _ensure(
                ContactCreate(
                    name=_fake_name(rng),
                    phone=_fake_phone(serial),
                    roles=[ContactRole.MEMBER],
                    address=_fake_address(rng),
                )
            )
        )

    for _ in range(_SELLER_COUNT):
        serial += 1
        # 賣方/寄售主**必須**有 national_id，否則收購階段會被擋下
        sellers.append(
            await _ensure(
                ContactCreate(
                    name=_fake_name(rng),
                    phone=_fake_phone(serial),
                    national_id=_make_national_id(rng),
                    roles=[ContactRole.SELLER, ContactRole.CONSIGNOR],
                    address=_fake_address(rng),
                )
            )
        )

    await session.commit()
    if not sellers:  # pragma: no cover - 上面已保證，這是最後防線
        raise SeedFailed("沒有任何賣方/寄售主，收購階段將無法進行")
    return {"members": members, "sellers": sellers}


# 露營二手品的分類與品名（docs/37 §7.6）。售價呈長尾：眾數 300–1,500，
# 長尾延伸到 15,000–30,000（高階帳篷等）。
_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("帳篷", ("兩房一廳帳", "圓頂帳", "隧道帳", "速搭帳")),
    ("天幕", ("蝶形天幕", "方形天幕", "六角天幕")),
    ("桌椅", ("蛋捲桌", "折疊椅", "月亮椅", "料理桌")),
    ("睡袋睡墊", ("羽絨睡袋", "化纖睡袋", "自動充氣墊", "蛋槽睡墊")),
    ("炊具爐具", ("卡式爐", "雙口爐", "荷蘭鍋", "鑄鐵鍋")),
    ("燈具", ("汽化燈", "LED 露營燈", "串燈")),
    ("收納推車", ("folding 推車", "裝備收納箱", "工具袋")),
    ("保冷保溫", ("硬式冰桶", "軟式保冷袋", "保溫壺")),
    ("服飾", ("防風外套", "登山褲", "羊毛襪")),
    ("配件零件", ("營釘", "營繩", "地布", "延長線")),
    ("兒童用品", ("兒童睡袋", "兒童折疊椅")),
)


def _pick_price(rng: random.Random) -> int:
    """長尾售價：多數 300–1,500，少數高階品沖到 15,000–30,000。"""
    roll = rng.random()
    if roll < 0.80:
        return rng.randrange(300, 1501, 50)
    if roll < 0.97:
        return rng.randrange(1500, 8001, 100)
    return rng.randrange(15000, 30001, 500)


async def seed_acquisitions(
    session: AsyncSession,
    store_id: int,
    manager_id: int,
    seller_ids: list[int],
    rng: random.Random,
    *,
    buyout_items: int,
    consignment_items: int,
) -> dict[str, int]:
    """收購入庫（相依鏈第 2 步）：買斷與寄售的序號單品。

    **走 `AcquisitionService.create_acquisition`**——鑑價、入庫、付現、購物金、
    寄售抽成預設值全部由 service 處理；直接塞表會產出通不過不變條件的假資料。

    收購付現需要**開帳中的 cash_session**（§7.2.1），故呼叫端須先開帳。
    """
    svc = AcquisitionService(session)
    default_commission_pct = (
        await StoreSettingsService(session).get_effective_settings(store_id)
    ).default_commission_pct
    made = {"buyout": 0, "consignment": 0}

    async def _one(kind: AcquisitionType, n: int) -> int:
        ok = 0
        for i in range(n):
            category, names = rng.choice(_CATEGORIES)
            listed = _pick_price(rng)
            # 收購成本以毛利率回推（定價計算機的反向），確保成本 < 售價
            cost = max(50, int(listed * rng.uniform(0.35, 0.62)))
            item = AcquisitionItemIn(
                name=f"{rng.choice(names)}（{category}）",
                grade=rng.choice([Grade.S, Grade.A, Grade.B, Grade.C, Grade.D]),
                listed_price=Decimal(listed),
                # 買斷才有收購成本；寄售的成本概念是抽成，兩者不可混填
                acquisition_cost=Decimal(cost) if kind is AcquisitionType.BUYOUT else None,
                # 寄售**每筆都必須帶抽成**（schema 強制）。取店家設定的預設值，
                # 不寫死 50——設定改了之後 seed 資料才不會與店家實際口徑脫節。
                commission_pct=(
                    None if kind is AcquisitionType.BUYOUT else default_commission_pct
                ),
            )
            data = AcquisitionCreate(
                type=kind,
                contact_id=rng.choice(seller_ids),
                items=[item],
                payout_method=PayoutMethod.CASH,
            )
            await svc.create_acquisition(
                store_id, manager_id, data, idempotency_key=f"seed-{kind.value}-{i}-{ok}"
            )
            ok += 1
        return ok

    made["buyout"] = await _one(AcquisitionType.BUYOUT, buyout_items)
    made["consignment"] = await _one(AcquisitionType.CONSIGNMENT, consignment_items)
    await session.commit()
    return made


async def purge(session: AsyncSession, store_id: int) -> None:
    """清除本腳本產生的資料（`--purge`）。

    以 `is_seed` 旗標為準（docs/37 §7.8）；尚未實作資料產生時為 no-op。
    """
    _ = store_id
    return None


async def run(
    *, rng_seed: int, do_purge: bool, buyout_items: int, consignment_items: int
) -> SeedReport:
    settings = get_settings()
    _guard_environment(settings.database_url)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        store_id, manager_id = await _require_prerequisites(session)
        if do_purge:
            await purge(session, store_id)
            await session.commit()
            return SeedReport(counts={"已清除": 1}, invariants=[])

        rng = random.Random(rng_seed)
        # 相依鏈第 1 步：聯絡人。後續的收購只能掛在有 national_id 的賣方身上。
        contacts = await seed_contacts(session, store_id, rng)

        # 相依鏈第 2 步：收購入庫。**付現需開帳中的 cash_session**（§7.2.1），先開帳。
        cash = CashDrawerService(session)
        if await cash.get_current_session(store_id) is None:
            await cash.open_session(store_id, manager_id, Decimal(20000))
            await session.commit()
        acquired = await seed_acquisitions(
            session,
            store_id,
            manager_id,
            contacts["sellers"],
            rng,
            buyout_items=buyout_items,
            consignment_items=consignment_items,
        )
        # TODO(P0 分段建置)：散裝批／一般商品 → 逐日班別＋銷售 → 寄售結算 → 退貨 → 簽名。

        report = SeedReport()
        report.counts["會員"] = len(contacts["members"])
        report.counts["賣方/寄售主"] = len(contacts["sellers"])
        report.counts["收購-買斷件數"] = acquired["buyout"]
        report.counts["收購-寄售件數"] = acquired["consignment"]
        report.counts["序號商品"] = await _count(
            session, "serialized_items", f"store_id = {store_id}"
        )
        report.counts["銷售"] = await _count(session, "sales", f"store_id = {store_id}")
        report.counts["銷售明細"] = await _count(session, "sale_lines", f"store_id = {store_id}")
        report.counts["聯絡人"] = await _count(session, "contacts", f"store_id = {store_id}")
        report.counts["現金班別"] = await _count(
            session, "cash_sessions", f"store_id = {store_id}"
        )
        report.invariants = await verify_invariants(session, store_id)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬營運資料（docs/37 P0）")
    parser.add_argument("--seed", type=int, default=42, help="固定亂數種子（相同種子＝相同資料）")
    parser.add_argument("--purge", action="store_true", help="清除本腳本產生的資料")
    # 分段建置期間可先跑小批量把整條路徑走通，再放大到目標量
    parser.add_argument("--buyout", type=int, default=10, help="買斷件數")
    parser.add_argument("--consignment", type=int, default=10, help="寄售件數")
    parser.add_argument(
        "--out",
        default="seed_verification.txt",
        help="驗證報告輸出路徑",
    )
    args = parser.parse_args()

    report = asyncio.run(
        run(
            rng_seed=args.seed,
            do_purge=args.purge,
            buyout_items=args.buyout,
            consignment_items=args.consignment,
        )
    )
    rendered = report.render()
    print(rendered)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    if not report.ok:
        print("\n不變條件未全數通過：seed 視為失敗。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
