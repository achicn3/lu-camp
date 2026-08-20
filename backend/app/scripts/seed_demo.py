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
from datetime import UTC, date, datetime, timedelta
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
from app.core.time import STORE_TIME_ZONE, store_date, utc_now
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.repository import ContactRepository
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import ContactService
from app.modules.inventory.models import CatalogProduct, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.menu.models import MenuItem
from app.modules.menu.service import MenuService
from app.modules.purchasing.models import Supplier
from app.modules.purchasing.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineCreate,
    ReceiveLineIn,
    SupplierCreate,
)
from app.modules.purchasing.service import PurchasingService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.service import SalesService
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    ContactRole,
    Grade,
    PayoutMethod,
    SaleLineType,
    SerializedItemStatus,
    ServiceMode,
    TenderType,
)
from app.shared.exceptions import DuplicateContact, DuplicateMenuItem

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
    rng_seed: int,
    buyout_items: int,
    consignment_items: int,
    batch_suffix: str = "",
    acquired_at: datetime | None = None,
) -> dict[str, int]:
    """收購入庫（相依鏈第 2 步）：買斷與寄售的序號單品。

    **走 `AcquisitionService.create_acquisition`**——鑑價、入庫、付現、購物金、
    寄售抽成預設值全部由 service 處理；直接塞表會產出通不過不變條件的假資料。

    收購付現需要**開帳中的 cash_session**（§7.2.1），故呼叫端須先開帳。
    """
    svc = AcquisitionService(session)
    # 批次識別：同一種子重跑會沿用既有資料（冪等重放），換種子則產生新批次
    batch = f"s{rng_seed}{f'-{batch_suffix}' if batch_suffix else ''}"
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
            # **冪等鍵必須帶批次識別**：只用序號的話，重跑（或換 --seed）會用同一把鍵
            # 配上不同內容 → IdempotencyKeyConflict。鍵一旦重複，冪等機制會把它當成
            # 「同一筆的重試」，這正是它該做的事——所以要讓不同批次的鍵天然不同。
            result = await svc.create_acquisition(
                store_id,
                manager_id,
                data,
                idempotency_key=f"seed-{batch}-{kind.value}-{i}",
            )
            if acquired_at is not None:
                # 收購也要回填時間，否則整年的收購全部落在今天
                acq = await session.get(Acquisition, result.acquisition_id)
                if acq is not None:
                    acq.created_at = acquired_at
            ok += 1
        return ok

    made["buyout"] = await _one(AcquisitionType.BUYOUT, buyout_items)
    made["consignment"] = await _one(AcquisitionType.CONSIGNMENT, consignment_items)
    await session.commit()
    return made


# 供應商（docs/19）。一般商品的進貨來源。
_SUPPLIERS: tuple[tuple[str, str], ...] = (
    ("山野戶外用品有限公司", "02-2345-6789"),
    ("野樂實業股份有限公司", "04-2278-1122"),
    ("大地露營器材行", "03-4567-8901"),
    ("極光貿易有限公司", "02-8765-4321"),
    ("溪畔食品批發", "05-2233-4455"),
    ("光野照明科技", "07-3344-5566"),
    ("台灣瓦斯罐工業", "06-2211-3344"),
    ("雲頂紡織有限公司", "04-8899-7766"),
)

# 一般商品（新品）：消耗品與小配件為主——這類會反覆補貨、反覆賣出，
# 正是序號品（一件一次）補不上的銷售量來源（§7.4.1）。
# (名稱, 售價, 進價, 補貨點)
_CATALOG_PRODUCTS: tuple[tuple[str, int, int, int], ...] = (
    ("高山瓦斯罐 230g", 180, 110, 24),
    ("高山瓦斯罐 450g", 280, 175, 18),
    ("卡式瓦斯罐 三入", 150, 92, 20),
    ("防風打火機", 120, 62, 15),
    ("營燈電池 AA 四入", 90, 45, 30),
    ("營燈電池 AAA 四入", 90, 45, 24),
    ("18650 充電電池", 350, 210, 10),
    ("營釘 鍛造 20cm 十入", 480, 290, 12),
    ("營釘 鋁合金 18cm 十入", 260, 150, 12),
    ("營繩 反光 4mm 20m", 220, 128, 15),
    ("營繩調節片 十入", 120, 60, 15),
    ("橡皮頭營槌", 450, 268, 8),
    ("鍛造營槌", 890, 520, 6),
    ("地布 300x300", 780, 460, 8),
    ("天幕營柱 240cm", 680, 395, 10),
    ("延長線 15m 防水", 850, 495, 8),
    ("摺疊桌 蛋捲 90cm", 1580, 920, 5),
    ("摺疊椅 克米特", 1880, 1120, 5),
    ("月亮椅", 1280, 750, 6),
    ("行軍床", 1980, 1180, 4),
    ("充氣睡墊 單人", 1450, 850, 6),
    ("自動充氣枕", 480, 275, 10),
    ("睡袋內套 棉質", 520, 300, 8),
    ("保溫壺 1L", 780, 450, 8),
    ("鈦杯 450ml", 690, 400, 8),
    ("不鏽鋼杯 三入", 320, 180, 12),
    ("鑄鐵鍋 26cm", 2280, 1350, 3),
    ("荷蘭鍋腳架", 880, 510, 5),
    ("烤盤 波浪紋", 980, 570, 6),
    ("露營餐具組 四人", 680, 390, 8),
    ("砧板 摺疊", 280, 155, 12),
    ("料理刀 附套", 450, 258, 8),
    ("燜燒罐 500ml", 890, 520, 6),
    ("保冷袋 20L", 780, 450, 6),
    ("冰磚 大", 180, 95, 20),
    ("水桶 摺疊 10L", 350, 198, 12),
    ("水袋 12L", 420, 240, 10),
    ("洗碗精 露營用", 120, 58, 24),
    ("菜瓜布 三入", 60, 28, 30),
    ("垃圾袋 露營用 十入", 80, 38, 30),
    ("防蚊液 敵避 12%", 280, 158, 20),
    ("防蚊手環 五入", 150, 78, 20),
    ("蚊香 盤裝", 90, 42, 24),
    ("急救包 基本款", 480, 275, 8),
    ("暖暖包 十入", 150, 72, 30),
    ("手電筒 頭燈", 580, 335, 10),
    ("營燈 充電式", 980, 570, 8),
    ("串燈 10m 暖白", 450, 260, 12),
    ("太陽能充電板 20W", 1880, 1120, 3),
    ("行動電源 20000mAh", 1280, 750, 5),
    ("木炭 3kg", 180, 92, 24),
    ("生火棒 十入", 120, 58, 24),
    ("噴槍 卡式", 680, 395, 8),
    ("柴火 相思木 5kg", 320, 175, 15),
    ("防火地墊", 580, 335, 8),
    ("焚火台 摺疊", 1480, 870, 4),
    ("排煙帳篷專用煙囪", 2280, 1350, 2),
    ("雨衣 連身", 380, 215, 12),
    ("雨鞋 中筒", 680, 395, 8),
    ("防水袋 20L", 420, 240, 10),
    ("快乾毛巾 大", 280, 155, 15),
    ("摺疊水盆", 320, 180, 12),
    ("曬衣繩 附夾", 180, 92, 15),
    ("露營車輪推車", 2680, 1580, 3),
    ("裝備收納箱 60L", 880, 510, 6),
    ("收納袋 分類六件組", 480, 275, 10),
    ("帳篷修補片", 150, 72, 15),
    ("防水噴劑", 380, 215, 12),
    ("帳篷清潔劑", 280, 158, 12),
    ("睡袋壓縮袋", 350, 198, 10),
    ("登山杖 一對", 1180, 690, 5),
    ("護膝 一對", 480, 275, 8),
    ("多功能工具鉗", 780, 450, 6),
    ("指南針", 320, 180, 8),
    ("哨子 求生", 90, 42, 20),
    ("反光背心", 180, 95, 15),
    ("摺疊鏟", 580, 335, 6),
    ("斧頭 手斧", 980, 570, 4),
    ("鋸子 摺疊", 680, 395, 6),
)


# 內用桌號。**沒有這一步就開不出內用單**：create_sale 會擋掉不在
# `settings.dine_in_tables` 裡的桌號，而該設定預設是空清單（docs/35）。
_DINE_IN_TABLES = ["A1", "A2", "A3", "A4", "B1", "B2", "C1", "C2"]

# 菜單（docs/35）。露營場邊小賣部的品項：熱飲、冷飲、簡餐、點心。
# 價格為含稅整數元（CLAUDE.md §6）。
_MENU_ITEMS: tuple[tuple[str, int, str], ...] = (
    ("美式咖啡", 80, "飲品"),
    ("拿鐵", 110, "飲品"),
    ("卡布奇諾", 110, "飲品"),
    ("熱可可", 100, "飲品"),
    ("錫蘭紅茶", 70, "飲品"),
    ("烏龍茶", 70, "飲品"),
    ("鮮奶茶", 90, "飲品"),
    ("氣泡水", 60, "飲品"),
    ("可樂", 40, "飲品"),
    ("運動飲料", 45, "飲品"),
    ("礦泉水", 25, "飲品"),
    ("生啤酒", 130, "飲品"),
    ("手沖單品", 150, "飲品"),
    ("柳橙汁", 80, "飲品"),
    ("檸檬紅茶", 80, "飲品"),
    ("牛肉麵", 180, "簡餐"),
    ("咖哩飯", 160, "簡餐"),
    ("炒泡麵", 120, "簡餐"),
    ("烤吐司總匯", 110, "簡餐"),
    ("熱狗堡", 90, "簡餐"),
    ("義大利肉醬麵", 170, "簡餐"),
    ("鹽酥雞", 100, "點心"),
    ("薯條", 70, "點心"),
    ("雞塊", 80, "點心"),
    ("烤香腸", 60, "點心"),
    ("棉花糖烤組", 120, "點心"),
    ("爆米花", 50, "點心"),
    ("鬆餅", 130, "點心"),
    ("冰淇淋", 60, "點心"),
    ("泡麵", 45, "點心"),
)


async def seed_settings(session: AsyncSession, store_id: int, manager_id: int) -> None:
    """持久化店家設定列，並維護內用桌號（相依鏈的前置步驟）。

    預設 `dine_in_tables` 是**空清單**，此時任何內用結帳都會被
    `create_sale` 擋下（docs/35：桌號必須是設定頁維護過的那幾桌）。
    """
    await StoreSettingsService(session).update_settings(
        store_id,
        actor_user_id=manager_id,
        patch=SettingsUpdateRequest(dine_in_tables=list(_DINE_IN_TABLES)),
    )
    await session.commit()


async def seed_menu(session: AsyncSession, store_id: int, manager_id: int) -> list[MenuItem]:
    """建立菜單品項；已存在同名者沿用（腳本可重跑）。"""
    svc = MenuService(session)
    items: list[MenuItem] = []
    for order, (name, price, category) in enumerate(_MENU_ITEMS):
        try:
            items.append(
                await svc.create_menu_item(
                    store_id,
                    name=name,
                    unit_price=Decimal(price),
                    category=category,
                    sort_order=order,
                    actor_user_id=manager_id,
                )
            )
        except DuplicateMenuItem:
            existing = await session.scalar(
                select(MenuItem).where(MenuItem.store_id == store_id, MenuItem.name == name)
            )
            if existing is not None:
                items.append(existing)
    await session.commit()
    return items


async def seed_suppliers(
    session: AsyncSession, store_id: int
) -> list[Supplier]:
    """建立供應商；已存在同名者沿用（腳本可重跑）。"""
    svc = PurchasingService(session)
    made: list[Supplier] = []
    for name, contact in _SUPPLIERS:
        existing = await session.scalar(
            select(Supplier).where(Supplier.store_id == store_id, Supplier.name == name)
        )
        if existing is not None:
            made.append(existing)
            continue
        made.append(await svc.create_supplier(store_id, SupplierCreate(name=name, contact=contact)))
    await session.commit()
    return made


async def seed_catalog(session: AsyncSession, store_id: int) -> list[CatalogProduct]:
    """建立一般商品（初始庫存 0，靠採購收貨補）；已存在同 SKU 者沿用。"""
    svc = InventoryService(session)
    made: list[CatalogProduct] = []
    for index, (name, price, _cost, reorder) in enumerate(_CATALOG_PRODUCTS):
        sku = f"NEW-{index + 1:04d}"
        existing = await session.scalar(
            select(CatalogProduct).where(
                CatalogProduct.store_id == store_id, CatalogProduct.sku == sku
            )
        )
        if existing is not None:
            made.append(existing)
            continue
        made.append(
            await svc.create_catalog(
                store_id,
                sku=sku,
                name=name,
                unit_price=Decimal(price),
                reorder_point=reorder,
            )
        )
    await session.commit()
    return made


async def restock_catalog(
    session: AsyncSession,
    store_id: int,
    manager_id: int,
    suppliers: list[Supplier],
    products: list[CatalogProduct],
    rng: random.Random,
    *,
    day: date,
    moment: datetime,
    cost_by_sku: dict[str, int],
) -> int:
    """對低於補貨點的商品開採購單並收貨（走完整 PO → 收貨流程）。

    **不直接改 `quantity_on_hand`**：庫存異動、成本快照（`unit_cost`＝最新進價）、
    收貨批次與採購單狀態機都靠這條路徑才會被真正觸發（docs/37 §7.2 黃金路徑走 service）。
    回傳本次收貨的採購單數。
    """
    low = [p for p in products if p.quantity_on_hand <= p.reorder_point]
    if not low:
        return 0
    svc = PurchasingService(session)
    made = 0
    # 依供應商分批開單：一張單一個供應商（現實就是這樣）
    rng.shuffle(low)
    for chunk_start in range(0, len(low), 12):
        chunk = low[chunk_start : chunk_start + 12]
        supplier = rng.choice(suppliers)
        lines = [
            PurchaseOrderLineCreate(
                catalog_product_id=product.id,
                qty=max(6, product.reorder_point * rng.choice([2, 3, 4])),
                unit_cost=Decimal(cost_by_sku[product.sku]),
            )
            for product in chunk
        ]
        order = await svc.create_purchase_order(
            store_id,
            PurchaseOrderCreate(supplier_id=supplier.id, lines=lines, submit=True),
            actor_user_id=manager_id,
        )
        await session.flush()
        receive_lines = [ReceiveLineIn(line_id=line.id, qty=line.qty) for line in order.lines]
        key = f"seed-recv-{day.isoformat()}-{order.id}"
        await svc.receive_purchase_order(
            store_id,
            order.id,
            actor_user_id=manager_id,
            lines=receive_lines,
            idempotency_key=key,
            request_fingerprint=key,
        )
        # 採購與收貨的時點也要回填，否則整年的進貨全落在今天
        order.created_at = moment
        for receipt in order.receipts:
            receipt.received_at = moment
        made += 1
    await session.commit()
    return made


# 餐飲季節性：與裝備**相反**——夏天露營旺、賣吃喝；冬天裝備旺、餐飲淡。
# 兩者高峰不同時間，共用一張季節表會讓全年曲線變成假的同步波動。
_FNB_SEASONALITY = {
    1: 0.75, 2: 0.85, 3: 0.95, 4: 1.10, 5: 1.15, 6: 1.20,
    7: 1.30, 8: 1.30, 9: 1.10, 10: 1.00, 11: 0.85, 12: 0.80,
}

# 內用佔比（docs/39 §3.2 的分母是「有餐飲的單」）。假日較多人坐下來吃。
_DINE_IN_SHARE_WEEKDAY = 0.50
_DINE_IN_SHARE_HOLIDAY = 0.62


def _fnb_sale_count(day: date, rng: random.Random, *, base: float) -> int:
    """某一營業日的餐飲組數。"""
    factor = _FNB_SEASONALITY[day.month]
    if day.weekday() >= 5:
        factor *= _HOLIDAY_MULTIPLIER
    return max(0, int(rng.gauss(base * factor, base * factor * 0.3)))


def _fnb_hour(day: date, rng: random.Random) -> int:
    """餐飲時段：午餐 11–13 與下午茶 14–17 兩個峰，晚間 18–20 次之。

    與裝備的時段（14–18）**刻意不同**——docs/39 的時段分佈報表若兩者同形，
    就看不出「餐飲有午餐峰」這個真實現象。
    """
    hours = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    weights = (
        [1, 4, 6, 5, 4, 4, 3, 3, 3, 2, 1]
        if day.weekday() >= 5
        else [1, 3, 5, 4, 3, 3, 2, 2, 2, 1, 1]
    )
    return rng.choices(hours, weights)[0]


def _fnb_basket(
    menu_items: list[MenuItem], rng: random.Random, *, dine_in: bool
) -> list[tuple[MenuItem, int]]:
    """一組客人點的東西：內用份數較多（坐下來吃），外帶偏飲品。"""
    n_kinds = rng.choices([1, 2, 3, 4], [4, 4, 2, 1] if dine_in else [6, 3, 1, 0])[0]
    picked = rng.sample(menu_items, min(n_kinds, len(menu_items)))
    return [(item, rng.choices([1, 2, 3], [6, 3, 1])[0]) for item in picked]


# 收購送件的季節性（§7.3）：3–5 月（露季結束出清）與 1 月（年前整理）為高峰
_INTAKE_SEASONALITY = {
    1: 1.6, 2: 0.8, 3: 1.5, 4: 1.5, 5: 1.4, 6: 0.7,
    7: 0.6, 8: 0.6, 9: 0.9, 10: 1.0, 11: 0.9, 12: 1.0,
}


# 營運節奏（docs/37 §7.3）
_HOLIDAY_MULTIPLIER = 2.7  # 週六日約平日 2.5–3 倍
# 裝備類季節性：10 月–翌年 2 月旺季，6–8 月淡季（約旺季 40%）
_GEAR_SEASONALITY = {
    1: 1.00, 2: 0.95, 3: 0.70, 4: 0.60, 5: 0.55, 6: 0.40,
    7: 0.40, 8: 0.45, 9: 0.70, 10: 1.00, 11: 1.05, 12: 1.00,
}


def _daily_sale_count(day: date, rng: random.Random, *, base: float) -> int:
    """某一營業日的銷售筆數：季節性 × 週間 × 隨機擾動。"""
    factor = _GEAR_SEASONALITY[day.month]
    if day.weekday() >= 5:
        factor *= _HOLIDAY_MULTIPLIER
    return max(0, int(rng.gauss(base * factor, base * factor * 0.25)))


def _business_hour(day: date, rng: random.Random) -> int:
    """時段分佈（§7.3）：裝備集中 14–18 時，假日尖峰 13–17 時。"""
    if day.weekday() >= 5:
        return rng.choices([11, 12, 13, 14, 15, 16, 17, 18], [1, 2, 4, 5, 5, 4, 3, 2])[0]
    return rng.choices([12, 13, 14, 15, 16, 17, 18, 19], [1, 2, 3, 4, 4, 3, 2, 1])[0]


async def seed_daily_sales(
    session: AsyncSession,
    store_id: int,
    manager_id: int,
    member_ids: list[int],
    seller_ids: list[int],
    menu_items: list[MenuItem],
    suppliers: list[Supplier],
    catalog: list[CatalogProduct],
    rng: random.Random,
    *,
    rng_seed: int,
    days: int,
    base_per_day: float,
    daily_intake: float,
    fnb_per_day: float,
    catalog_per_day: float,
) -> dict[str, int]:
    """逐日「開帳 → 當日銷售 → 結帳」（相依鏈第 3 步，docs/37 §7.2.1）。

    **不可沿用既有 seed_dev_demo 的手法**（只開一個班別、全部銷售掛其下再回填日期）：
    §7.4 要 305–400 個歷史班別，且不變條件 #4 的現金等式要 **per-session** 成立。

    今日的班別**留給呼叫端決定**是否結掉——手冊的 `02-cash-open` 需要「尚未開帳」，
    但儀表板又需要今日有交易（§2.2 的定案：seed 今日交易後把當日班別結掉）。
    """
    sales_svc = SalesService(session)
    cash = CashDrawerService(session)
    made = {
        "sales": 0, "sessions": 0, "buyout": 0, "consignment": 0,
        "dine_in": 0, "takeout": 0, "purchase_orders": 0, "catalog_lines": 0,
    }
    cost_by_sku = {
        f"NEW-{index + 1:04d}": cost
        for index, (_name, _price, cost, _reorder) in enumerate(_CATALOG_PRODUCTS)
    }
    today = store_date(utc_now())

    # **一次撈出可售清單再逐一取用**：每筆銷售各查一次資料庫，在上萬筆時是主要成本。
    pool = list(
        await session.scalars(
            select(SerializedItem)
            .where(
                SerializedItem.store_id == store_id,
                SerializedItem.status == SerializedItemStatus.IN_STOCK,
            )
            .order_by(SerializedItem.id)
        )
    )
    rng.shuffle(pool)
    cursor = 0

    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        if day.weekday() == 1 and rng.random() < 0.5:  # 週二不定期公休
            continue
        n = _daily_sale_count(day, rng, base=base_per_day)
        n_fnb = _fnb_sale_count(day, rng, base=fnb_per_day) if menu_items else 0
        n_catalog = _daily_sale_count(day, rng, base=catalog_per_day) if catalog else 0
        if n == 0 and n_fnb == 0 and n_catalog == 0:
            continue

        # 每一天都是獨立班別：先結掉殘留的，再開今天的
        current = await cash.get_current_session(store_id)
        if current is not None:
            await cash.close_session(current, await cash.expected_amount(current), manager_id)
            await session.commit()
        opened = await cash.open_session(store_id, manager_id, Decimal(30000))
        # **班別時間也要回填**：只回填 sale.created_at 的話，所有班別都會擠在今天，
        # 現金對帳報表與手冊的每日對帳截圖就全部失真（實測踩過）。
        opened.opened_at = datetime(
            day.year, day.month, day.day, 9, 30, tzinfo=STORE_TIME_ZONE
        ).astimezone(UTC)
        await session.commit()
        made["sessions"] += 1

        # 補貨：低於補貨點就開採購單並收貨。**走完整 PO → 收貨流程**，
        # 庫存異動、成本快照與採購單狀態機才會真的被觸發。
        if catalog and suppliers:
            made["purchase_orders"] += await restock_catalog(
                session, store_id, manager_id, suppliers, catalog, rng,
                day=day, moment=opened.opened_at, cost_by_sku=cost_by_sku,
            )

        # **當日收購與當日銷售共用同一個班別**——現實中收購付現與銷售收現就是同一個抽屜。
        # 先前把收購全部塞進單一班別，該班別的應有現金變成 −720 萬（實測踩過）。
        intake = max(0, int(rng.gauss(daily_intake * _INTAKE_SEASONALITY[day.month], 1.5)))
        if intake > 0:
            acquired_today = await seed_acquisitions(
                session,
                store_id,
                manager_id,
                seller_ids,
                rng,
                rng_seed=rng_seed,
                buyout_items=max(1, int(intake * 0.75)),
                consignment_items=max(0, intake - int(intake * 0.75)),
                batch_suffix=day.isoformat(),
                acquired_at=opened.opened_at,
            )
            made["buyout"] += acquired_today["buyout"]
            made["consignment"] += acquired_today["consignment"]
            # 當日新入庫的也要進可售池（今天收、今天就可能賣出）
            fresh = list(
                await session.scalars(
                    select(SerializedItem).where(
                        SerializedItem.store_id == store_id,
                        SerializedItem.status == SerializedItemStatus.IN_STOCK,
                        SerializedItem.id.not_in([p.id for p in pool[cursor:]] or [0]),
                    )
                )
            )
            pool.extend(i for i in fresh if i.status == SerializedItemStatus.IN_STOCK)

        for i in range(n):
            if cursor >= len(pool):  # 庫存賣完就停止（不是錯誤，但要讓呼叫端看得到）
                break
            item = pool[cursor]
            cursor += 1
            moment = datetime(
                day.year, day.month, day.day,
                _business_hour(day, rng), rng.randrange(60), tzinfo=STORE_TIME_ZONE,
            ).astimezone(UTC)
            sale = await sales_svc.create_sale(
                store_id,
                manager_id,
                lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
                tenders=[TenderInput(tender_type=TenderType.CASH, amount=item.listed_price)],
                buyer_contact_id=(rng.choice(member_ids) if rng.random() < 0.45 else None),
                idempotency_key=f"seed-sale-{day.isoformat()}-{i}",
            )
            # **時間序靠回填**：service 以 now() 落地，報表則一律以 created_at 篩選
            sale.created_at = moment
            made["sales"] += 1

        # **一般商品**：可補貨，故同一個 SKU 能反覆賣出（與序號品的「一件一次」相反）。
        for i in range(n_catalog):
            in_stock = [p for p in catalog if p.quantity_on_hand > 0]
            if not in_stock:
                break
            n_kinds = min(rng.choices([1, 2, 3], [5, 3, 2])[0], len(in_stock))
            cat_basket: list[tuple[CatalogProduct, int]] = []
            for product in rng.sample(in_stock, n_kinds):
                qty = min(product.quantity_on_hand, rng.choices([1, 2, 3], [7, 2, 1])[0])
                if qty > 0:
                    cat_basket.append((product, qty))
            if not cat_basket:
                continue
            amount = sum((p.unit_price * qty for p, qty in cat_basket), Decimal(0))
            moment = datetime(
                day.year, day.month, day.day,
                _business_hour(day, rng), rng.randrange(60), tzinfo=STORE_TIME_ZONE,
            ).astimezone(UTC)
            sale = await sales_svc.create_sale(
                store_id,
                manager_id,
                lines=[
                    SaleLineInput(
                        line_type=SaleLineType.CATALOG, catalog_product_id=p.id, qty=qty
                    )
                    for p, qty in cat_basket
                ],
                tenders=[TenderInput(tender_type=TenderType.CASH, amount=amount)],
                buyer_contact_id=(rng.choice(member_ids) if rng.random() < 0.40 else None),
                idempotency_key=f"seed-cat-{day.isoformat()}-{i}",
            )
            sale.created_at = moment
            made["sales"] += 1
            made["catalog_lines"] += len(cat_basket)

        # **餐飲**：不扣庫存，故不受收購量牽制——這正是銷售量的主要來源（docs/37 §7.4.1）。
        for i in range(n_fnb):
            holiday = day.weekday() >= 5
            share = _DINE_IN_SHARE_HOLIDAY if holiday else _DINE_IN_SHARE_WEEKDAY
            dine_in = rng.random() < share
            basket = _fnb_basket(menu_items, rng, dine_in=dine_in)
            amount = sum((item.unit_price * qty for item, qty in basket), Decimal(0))
            moment = datetime(
                day.year, day.month, day.day,
                _fnb_hour(day, rng), rng.randrange(60), tzinfo=STORE_TIME_ZONE,
            ).astimezone(UTC)
            sale = await sales_svc.create_sale(
                store_id,
                manager_id,
                lines=[
                    SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=item.id, qty=qty)
                    for item, qty in basket
                ],
                tenders=[TenderInput(tender_type=TenderType.CASH, amount=amount)],
                # 外帶不累點、不折扣、不可用購物金（docs/35 §1.1），故不掛會員
                buyer_contact_id=(
                    rng.choice(member_ids) if dine_in and rng.random() < 0.30 else None
                ),
                service_mode=ServiceMode.DINE_IN if dine_in else ServiceMode.TAKEOUT,
                table_no=rng.choice(_DINE_IN_TABLES) if dine_in else None,
                idempotency_key=f"seed-fnb-{day.isoformat()}-{i}",
            )
            sale.created_at = moment
            made["sales"] += 1
            made["dine_in" if dine_in else "takeout"] += 1
        await session.commit()

        # 當日結帳；counted 由 service 算出的 expected 決定（variance=0）
        opened = await cash.get_current_session(store_id) or opened
        await cash.close_session(opened, await cash.expected_amount(opened), manager_id)
        opened.closed_at = datetime(
            day.year, day.month, day.day, 21, 0, tzinfo=STORE_TIME_ZONE
        ).astimezone(UTC)
        await session.commit()

    return made


async def purge(session: AsyncSession, store_id: int) -> None:
    """清除本腳本產生的資料（`--purge`）。

    以 `is_seed` 旗標為準（docs/37 §7.8）；尚未實作資料產生時為 no-op。
    """
    _ = store_id
    return None


async def run(
    *,
    rng_seed: int,
    do_purge: bool,
    days: int,
    base_per_day: float,
    daily_intake: float,
    fnb_per_day: float,
    catalog_per_day: float,
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
        # 相依鏈第 0 步：設定。內用桌號沒維護的話，**任何內用單都開不出來**。
        await seed_settings(session, store_id, manager_id)
        # 相依鏈第 1 步：聯絡人。後續的收購只能掛在有 national_id 的賣方身上。
        contacts = await seed_contacts(session, store_id, rng)
        # 相依鏈第 2 步：菜單。餐飲不扣庫存，是銷售量的主要來源（§7.4.1）。
        menu_items = await seed_menu(session, store_id, manager_id)
        # 供應商 → 一般商品（初始庫存 0，靠每日補貨的採購收貨進貨）
        suppliers = await seed_suppliers(session, store_id)
        catalog = await seed_catalog(session, store_id)

        # 相依鏈第 3 步：**逐日開帳 → 當日收購 → 當日銷售 → 結帳**。
        # 收購與銷售共用當日班別（現實中就是同一個抽屜），現金等式才 per-session 成立。
        daily = await seed_daily_sales(
            session,
            store_id,
            manager_id,
            contacts["members"],
            contacts["sellers"],
            menu_items,
            suppliers,
            catalog,
            rng,
            rng_seed=rng_seed,
            days=days,
            base_per_day=base_per_day,
            daily_intake=daily_intake,
            fnb_per_day=fnb_per_day,
            catalog_per_day=catalog_per_day,
        )
        acquired = {"buyout": daily["buyout"], "consignment": daily["consignment"]}
        # TODO(P0 分段建置)：散裝批／一般商品 → 寄售結算 → 退貨 → 簽名任務。

        report = SeedReport()
        report.counts["會員"] = len(contacts["members"])
        report.counts["賣方/寄售主"] = len(contacts["sellers"])
        report.counts["收購-買斷件數"] = acquired["buyout"]
        report.counts["收購-寄售件數"] = acquired["consignment"]
        report.counts["序號商品"] = await _count(
            session, "serialized_items", f"store_id = {store_id}"
        )
        report.counts["菜單品項"] = len(menu_items)
        report.counts["供應商"] = len(suppliers)
        report.counts["一般商品"] = len(catalog)
        report.counts["採購單"] = daily["purchase_orders"]
        report.counts["銷售"] = daily["sales"]
        report.counts["餐飲-內用組數"] = daily["dine_in"]
        report.counts["餐飲-外帶組數"] = daily["takeout"]
        report.counts["現金班別"] = daily["sessions"]
        report.counts["銷售明細"] = await _count(session, "sale_lines", f"store_id = {store_id}")
        report.counts["聯絡人"] = await _count(session, "contacts", f"store_id = {store_id}")
        report.invariants = await verify_invariants(session, store_id)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬營運資料（docs/37 P0）")
    parser.add_argument("--seed", type=int, default=42, help="固定亂數種子（相同種子＝相同資料）")
    parser.add_argument("--purge", action="store_true", help="清除本腳本產生的資料")
    # 分段建置期間可先跑小批量把整條路徑走通，再放大到目標量
    parser.add_argument("--intake", type=float, default=3.0, help="每個營業日平均收購件數")
    parser.add_argument("--days", type=int, default=3, help="回溯的營業天數")
    parser.add_argument("--per-day", type=float, default=8.0, help="平日基準裝備銷售筆數")
    parser.add_argument("--fnb-per-day", type=float, default=6.0, help="平日基準餐飲組數")
    parser.add_argument(
        "--catalog-per-day", type=float, default=5.0, help="平日基準一般商品銷售筆數"
    )
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
            days=args.days,
            base_per_day=args.per_day,
            daily_intake=args.intake,
            fnb_per_day=args.fnb_per_day,
            catalog_per_day=args.catalog_per_day,
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
