"""大規模、真實命名的營運模擬 seed（QA；非正式環境）。

以真實露營品牌/商品命名，灌入 2200+ 會員、3300+ 銷售、多檔活動、80+ 採購單、購物金，
跨 365 天鋪時間序。銷售一律經 SalesService 確保金額/毛利/結算口徑正確；created_at 回填過去。

前置：seed_dev_store（store id=1）+ seed_dev_user（dev-manager）。
執行：
    cd backend && ALLOW_DEV_SEED=true DATABASE_URL=...lucamp_e2e \
      uv run python -m qa_e2e.seed_large
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

import app.main  # noqa: F401
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import (
    Brand,
    BulkLot,
    CatalogProduct,
    Category,
    ProductModel,
    SerializedItem,
)
from app.modules.menu.service import MenuService
from app.modules.purchasing.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineCreate,
    ReceiveLineIn,
    SupplierCreate,
)
from app.modules.purchasing.service import PurchasingService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    TenderType,
)

_ALLOWED = frozenset({"development", "test"})
_RNG = random.Random(20260625)
_NOW = datetime.now(UTC)
N_MEMBERS = 2200
N_CONSIGNORS = 80
N_OWNED_ITEMS = 620
N_CONSIGNMENT_ITEMS = 320
N_BULK_LOTS = 36
N_PURCHASE_ORDERS = 80
N_CREDITED_MEMBERS = 600
N_DAILY_SALES = 3100
N_LIVE_CAMPAIGN_SALES = 460
N_STORE_CREDIT_SALES = 200

# ── 真實品牌與其二手裝備（序號品） ──────────────────────────────
BRAND_GEAR: dict[str, list[tuple[str, int]]] = {
    "Snow Peak": [
        ("焚火台 L", 6800),
        ("Amenity Dome M 帳篷", 12800),
        ("IGT 系統桌", 9500),
        ("鈦金屬杯 450", 1200),
        ("地火 PRO 套組", 8800),
    ],
    "Coleman": [
        ("氣化燈 286A", 3200),
        ("Tough Wide Dome 帳篷", 7800),
        ("頂級瓦斯爐", 2600),
        ("鋼甲軍刀冰箱 54L", 4500),
        ("經典暖爐", 3800),
    ],
    "MSR": [
        ("PocketRocket 2 爐頭", 1800),
        ("Hubba Hubba NX 帳篷", 13800),
        ("WindBurner 套鍋", 4200),
        ("Guardian 濾水器", 9800),
    ],
    "Nordisk": [("Asgard 12.6 帳篷", 32000), ("Utgard 13.2 帳篷", 28000), ("Alme 羊毛毯", 3600)],
    "Helinox": [
        ("Chair One 椅", 3200),
        ("Cot One 行軍床", 7800),
        ("Table One 桌", 2800),
        ("Sunset Chair 椅", 4200),
    ],
    "Naturehike": [
        ("雲尚 2 帳篷", 2800),
        ("輕量化睡袋", 1600),
        ("自動充氣睡墊", 1400),
        ("鋁合金桌", 1200),
    ],
    "Goal Zero": [("Yeti 500X 行動電源", 18000), ("Lighthouse 600 營燈", 2200)],
    "Jetboil": [("Flash 個人爐", 3600), ("MiniMo 爐具", 4800)],
    "DOD": [
        ("一房一廳帳", 9800),
        ("營舞者 T 帳", 8800),
        ("變形蟲營柱", 1800),
        ("廚房料理桌", 3200),
    ],
    "Mont-bell": [
        ("Down Hugger 800 #3 睡袋", 8800),
        ("Permafrost 羽絨外套", 9200),
        ("登山雨衣", 4200),
    ],
    "Therm-a-Rest": [("NeoAir XTherm 睡墊", 7200), ("Z Lite 摺疊睡墊", 2400)],
    "Black Diamond": [("Spot 400 頭燈", 1600), ("Distance 登山杖", 3800)],
    "Nemo": [("Dagger OSMO 2P 帳篷", 14800), ("Stargaze 月亮椅", 6800)],
    "UNIFLAME": [("民炊鍋具組", 3200), ("焚火台 折疊", 2800), ("分離式雙口爐", 4600)],
    "SOTO": [("蜘蛛爐 ST-310", 1600), ("氣化燈 虫の音", 4200)],
    "Logos": [("Tepee 印地安帳", 6800), ("保冷袋 25L", 1800), ("LED 串燈", 900)],
    "Captain Stag": [("鹿牌折疊桌", 1400), ("不鏽鋼杯組", 800)],
    "Kovea": [("方型爐 KGR", 2600), ("雙頭爐 Slim Twin", 3800)],
    "Sea to Summit": [("輕量鍋具 X-Pot", 2200), ("乾燥袋組", 1200)],
    "Quechua": [("快開帳 2 秒帳", 3200), ("保溫瓶 0.8L", 600)],
}

# ── 數量型商品（消耗品；高週轉，撐起銷售筆數） ──────────────────
CATALOG_ITEMS: list[tuple[str, str, int]] = [
    ("GAS-HI-230", "高山瓦斯罐 230g", 150),
    ("GAS-HI-450", "高山瓦斯罐 450g", 260),
    ("GAS-CASS-250", "卡式瓦斯罐 250g", 80),
    ("CHARCOAL-3KG", "天然備長炭 3kg", 320),
    ("FIRE-START", "防風打火機", 120),
    ("PEG-FORGE", "鍛造營釘 30cm 四入", 360),
    ("ROPE-REFLECT", "反光營繩 4mm 4M", 120),
    ("LANTERN-OIL", "煤油 1L", 180),
    ("MOSQUITO", "天然防蚊液", 250),
    ("HANDWARMER", "暖暖包 10入", 150),
    ("WET-WIPE", "濕紙巾 80抽", 60),
    ("TRASH-BAG", "露營大垃圾袋 20入", 90),
    ("FOIL", "加厚鋁箔紙", 110),
    ("DISH-SOAP", "天然洗碗精", 140),
    ("TABLE-CLOTH", "防水桌布", 280),
    ("CHAIR-COVER", "椅套 通用", 320),
    ("WATERPROOF-SPRAY", "防水噴霧 300ml", 380),
    ("GUY-LINE-LED", "營繩警示燈", 160),
    ("SANDBAG", "重力沙袋 4入", 420),
    ("GROUND-SHEET", "地布 300x300", 880),
    ("COOLER-ICE", "保冷劑 大", 130),
    ("BBQ-NET", "烤肉網 替換", 90),
    ("SKEWER", "不鏽鋼烤肉叉 6入", 180),
    ("PAPER-PLATE", "環保餐盤 50入", 120),
    ("INSECT-COIL", "天然防蚊香", 100),
    ("ROPE-MAIN", "主繩 6mm 10M", 350),
    ("LED-STRIP", "露營氣氛燈串", 320),
    ("BATTERY-D", "D 電池 4入", 200),
    ("FUEL-SOLID", "固體燃料 8入", 90),
    ("SALT-LAMP", "鹽燈蠟燭 6入", 160),
]

# ── 餐飲菜單（內用） ─────────────────────────────────────────────
MENU_ITEMS: list[tuple[str, int, str]] = [
    ("手沖單品咖啡", 150, "咖啡"),
    ("美式咖啡", 100, "咖啡"),
    ("拿鐵", 130, "咖啡"),
    ("卡布奇諾", 130, "咖啡"),
    ("熱可可", 120, "飲品"),
    ("伯爵奶茶", 110, "飲品"),
    ("氣泡水", 80, "飲品"),
    ("精釀啤酒", 180, "飲品"),
    ("柳橙汁", 90, "飲品"),
    ("奶油鬆餅", 160, "點心"),
    ("原味司康", 90, "點心"),
    ("肉桂捲", 110, "點心"),
    ("巴斯克乳酪蛋糕", 140, "點心"),
    ("烤棉花糖組", 120, "點心"),
    ("起司烤吐司", 100, "輕食"),
    ("熱壓三明治", 150, "輕食"),
    ("關東煮拼盤", 180, "輕食"),
    ("泡麵加蛋", 90, "輕食"),
    ("玉米濃湯", 80, "輕食"),
    ("炙燒骰子牛", 280, "主食"),
    ("野菜咖哩飯", 220, "主食"),
    ("辣味雞翅 6 隻", 200, "主食"),
    ("綜合堅果", 120, "零食"),
    ("洋芋片", 60, "零食"),
    ("黑巧克力", 90, "零食"),
]

SURNAMES = list(
    "陳林黃張李王吳劉蔡楊許鄭謝郭洪曾邱廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游詹胡施沈余趙盧梁"
)
GIVEN = [
    "志明",
    "淑芬",
    "家豪",
    "美玲",
    "俊傑",
    "雅婷",
    "建宏",
    "怡君",
    "宗翰",
    "詩涵",
    "冠廷",
    "欣怡",
    "承恩",
    "佳穎",
    "宇翔",
    "心怡",
    "柏翰",
    "思妤",
    "彥廷",
    "郁婷",
    "智偉",
    "曉君",
    "孟翰",
    "筱涵",
    "建志",
    "佩珊",
    "明哲",
    "雅雯",
    "冠宇",
    "婉婷",
    "勝凱",
    "玉婷",
    "俊賢",
    "靜宜",
    "柏宇",
    "馨儀",
    "家銘",
    "惠雯",
    "志豪",
    "品妤",
]

SUPPLIERS = [
    "山野貿易",
    "戶外王國際",
    "野營補給站",
    "露營家批發",
    "極地裝備有限公司",
    "登山者商行",
    "風和日麗戶外",
    "綠野仙蹤代理",
    "頂級露營進口",
    "森活選物",
    "瓦斯能源行",
    "炭火工坊",
]


def _back(d_from: int, d_to: int) -> datetime:
    day = _RNG.uniform(d_to, d_from)
    return _NOW - timedelta(days=day, hours=_RNG.uniform(-5, 5))


async def _seed() -> None:
    settings = get_settings()
    if settings.app_env not in _ALLOWED:
        raise SystemExit(f"拒絕：APP_ENV={settings.app_env}")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需 ALLOW_DEV_SEED=true")

    sm = get_sessionmaker()
    async with sm() as session:
        store = await session.scalar(select(Store).where(Store.id == 1))
        clerk = await session.scalar(select(User).where(User.username == "dev-manager"))
        if store is None or clerk is None:
            raise SystemExit("請先跑 seed_dev_store 與 seed_dev_user")
        store_id, clerk_id = store.id, clerk.id

        cash = CashDrawerService(session)
        if await cash.get_current_session(store_id) is None:
            await cash.open_session(store_id, clerk_id, Decimal(8000))
            await session.commit()
        await StoreSettingsService(session).update_settings(
            store_id,
            actor_user_id=clerk_id,
            patch=SettingsUpdateRequest(monthly_fixed_cash_outflow=Decimal(120000)),
        )
        await session.commit()

        # 品牌 / 型號 / 分類
        brand_ids: dict[str, int] = {}
        for bname in BRAND_GEAR:
            b = Brand(store_id=store_id, name=bname)
            session.add(b)
            await session.flush()
            brand_ids[bname] = b.id
        cats = [
            ("帳篷", 40),
            ("睡眠系統", 42),
            ("桌椅", 45),
            ("照明", 48),
            ("炊事", 45),
            ("消耗品", 35),
            ("收納", 50),
            ("服飾", 50),
        ]
        cat_ids: list[int] = []
        for cname, margin in cats:
            c = Category(store_id=store_id, name=cname, target_margin_pct=margin)
            session.add(c)
            await session.flush()
            cat_ids.append(c.id)
        for bname, gears in BRAND_GEAR.items():
            for gname, _ in gears:
                session.add(ProductModel(store_id=store_id, brand_id=brand_ids[bname], name=gname))
        await session.commit()

        # 數量型商品
        catalog_ids: list[int] = []
        for sku, name, price in CATALOG_ITEMS:
            p = CatalogProduct(
                store_id=store_id,
                sku=sku,
                name=name,
                unit_price=Decimal(price),
                quantity_on_hand=5000,
                reorder_point=_RNG.choice([20, 30, 50]),
            )
            session.add(p)
            await session.flush()
            catalog_ids.append(p.id)
        await session.commit()

        # 餐飲菜單
        menu_svc = MenuService(session)
        menu_ids: list[int] = []
        for i, (name, price, cat) in enumerate(MENU_ITEMS):
            mi = await menu_svc.create_menu_item(
                store_id,
                name=name,
                unit_price=Decimal(price),
                category=cat,
                sort_order=i,
                actor_user_id=clerk_id,
            )
            menu_ids.append(mi.id)
        await session.commit()

        # 供應商
        purch = PurchasingService(session)
        supplier_ids: list[int] = []
        for sname in SUPPLIERS:
            s = await purch.create_supplier(store_id, SupplierCreate(name=sname))
            supplier_ids.append(s.id)
        await session.commit()

        # 會員（一年以上營運量，真實姓名、唯一電話）
        member_ids: list[int] = []
        for i in range(N_MEMBERS):
            name = _RNG.choice(SURNAMES) + _RNG.choice(GIVEN)
            phone = "09" + str(10_000_000 + i)
            m = Contact(store_id=store_id, name=name, phone=phone, roles=["MEMBER"])
            session.add(m)
            if i % 400 == 399:
                await session.flush()
        await session.commit()
        member_ids = [
            r[0]
            for r in (
                await session.execute(
                    select(Contact.id).where(Contact.roles.contains(["MEMBER"]))
                )
            ).all()
        ]

        # 寄售人（含假 national_id_enc，沿 demo 直插法）
        consignor_ids: list[int] = []
        for i in range(N_CONSIGNORS):
            name = _RNG.choice(SURNAMES) + _RNG.choice(GIVEN)
            phone = "098" + str(1_000_000 + i)
            consignor = Contact(
                store_id=store_id,
                name=name,
                phone=phone,
                roles=["CONSIGNOR", "SELLER"],
                national_id_enc=f"enc-con-{i}",
            )
            session.add(consignor)
            await session.flush()
            consignor_ids.append(consignor.id)
        await session.commit()

        # 序號品（二手裝備，真實命名；自有 + 寄售）
        gear_pool = [(b, g, price) for b, gears in BRAND_GEAR.items() for g, price in gears]
        owned_codes: list[str] = []
        consign_codes: list[str] = []
        for i in range(N_OWNED_ITEMS):
            bname, gname, base = _RNG.choice(gear_pool)
            grade = _RNG.choice([Grade.S, Grade.A, Grade.A, Grade.B, Grade.B, Grade.C])
            wear = {"S": 0.85, "A": 0.7, "B": 0.55, "C": 0.4, "D": 0.3}[grade.value]
            listed = int(base * wear / 10) * 10
            si = SerializedItem(
                store_id=store_id,
                item_code=f"S{store_id}-OWN{i:04d}",
                name=f"{bname} {gname}",
                brand_id=brand_ids[bname],
                grade=grade,
                ownership_type=OwnershipType.OWNED,
                acquisition_cost=Decimal(int(listed * _RNG.uniform(0.45, 0.65))),
                listed_price=Decimal(listed),
                status=SerializedItemStatus.IN_STOCK,
                category_id=_RNG.choice(cat_ids),
            )
            session.add(si)
            owned_codes.append(si.item_code)
        for i in range(N_CONSIGNMENT_ITEMS):
            bname, gname, base = _RNG.choice(gear_pool)
            grade = _RNG.choice([Grade.S, Grade.A, Grade.B])
            wear = {"S": 0.88, "A": 0.72, "B": 0.58}[grade.value]
            listed = int(base * wear / 10) * 10
            si = SerializedItem(
                store_id=store_id,
                item_code=f"S{store_id}-CON{i:04d}",
                name=f"{bname} {gname}",
                brand_id=brand_ids[bname],
                grade=grade,
                ownership_type=OwnershipType.CONSIGNMENT,
                consignor_id=_RNG.choice(consignor_ids),
                commission_pct=_RNG.choice([40, 45, 50, 55]),
                listed_price=Decimal(listed),
                status=SerializedItemStatus.IN_STOCK,
                category_id=_RNG.choice(cat_ids),
            )
            session.add(si)
            consign_codes.append(si.item_code)
        # 散裝批（E 級）
        bulk_ids: list[int] = []
        bulk_names = [
            "二手營釘混合一批",
            "雜項配件零件",
            "二手鍋具零件",
            "營繩扣具一批",
            "備品螺絲五金",
            "二手燈具配件",
        ]
        for i in range(N_BULK_LOTS):
            total = _RNG.choice([300, 500, 800])
            unit = _RNG.choice([30, 50, 80, 120])
            lot = BulkLot(
                store_id=store_id,
                lot_code=f"L{store_id}-{i:03d}",
                name=_RNG.choice(bulk_names),
                grade=Grade.E,
                acquisition_cost=Decimal(int(unit * total * _RNG.uniform(0.4, 0.6))),
                acquisition_basis=BulkAcquisitionBasis.BAG,
                unit_price=Decimal(unit),
                total_qty=total,
                remaining_qty=total,
                status=BulkLotStatus.ON_SALE,
                category_id=_RNG.choice(cat_ids),
            )
            session.add(lot)
            await session.flush()
            bulk_ids.append(lot.id)
        await session.commit()

        # 採購單（80+ 張，多數收貨，保留部分未收貨）
        n_po = 0
        n_recv = 0
        for _ in range(N_PURCHASE_ORDERS):
            sup = _RNG.choice(supplier_ids)
            picks = _RNG.sample(catalog_ids, _RNG.randint(2, 5))
            lines = [
                PurchaseOrderLineCreate(
                    catalog_product_id=pid,
                    qty=_RNG.choice([20, 50, 100]),
                    unit_cost=Decimal(_RNG.choice([40, 60, 90, 120])),
                )
                for pid in picks
            ]
            po = await purch.create_purchase_order(
                store_id,
                PurchaseOrderCreate(supplier_id=sup, lines=lines, submit=True),
                actor_user_id=clerk_id,
            )
            await session.commit()
            n_po += 1
            if _RNG.random() < 0.8:
                receive_lines = [ReceiveLineIn(line_id=ln.id, qty=ln.qty) for ln in po.lines]
                await purch.receive_purchase_order(
                    store_id,
                    po.id,
                    actor_user_id=clerk_id,
                    lines=receive_lines,
                    idempotency_key=uuid4().hex,
                    request_fingerprint=f"seed-{po.id}",
                )
                await session.commit()
                n_recv += 1

        # 購物金：撥入給部分會員
        sc = StoreCreditService(session)
        credited_members: list[int] = _RNG.sample(member_ids, N_CREDITED_MEMBERS)
        for idx, cid in enumerate(credited_members):
            await sc.adjust(
                store_id,
                cid,
                amount=Decimal(_RNG.choice([200, 300, 500, 1000])),
                reason="開卡禮 / 活動回饋",
                created_by=clerk_id,
                idempotency_key=f"seed-credit-{idx}",
            )
            if idx % 100 == 99:
                await session.commit()
        await session.commit()

        # 銷售（3300+ 筆，跨 365 天，會員歸戶，多檔活動，部分購物金折抵）
        sales_svc = SalesService(session)
        owned_pool = list(owned_codes)
        sc_reserved = list(consign_codes[-N_STORE_CREDIT_SALES:])
        consign_pool = list(consign_codes[:-N_STORE_CREDIT_SALES])
        _RNG.shuffle(owned_pool)
        _RNG.shuffle(consign_pool)
        n_sales = n_void = n_sc_pay = 0

        async def one_sale(when: datetime, key: str, *, allow_menu: bool = True) -> int | None:
            nonlocal n_sales
            lines: list[SaleLineInput] = []
            for _ in range(_RNG.randint(1, 4)):
                roll = _RNG.random()
                if roll < 0.12 and owned_pool:
                    lines.append(
                        SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=owned_pool.pop())
                    )
                elif roll < 0.18 and consign_pool:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.SERIALIZED, item_code=consign_pool.pop()
                        )
                    )
                elif roll < 0.30:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.BULK_LOT,
                            bulk_lot_id=_RNG.choice(bulk_ids),
                            qty=_RNG.randint(1, 5),
                        )
                    )
                elif roll < 0.55 and allow_menu:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.MENU,
                            menu_item_id=_RNG.choice(menu_ids),
                            qty=_RNG.randint(1, 3),
                        )
                    )
                else:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.CATALOG,
                            catalog_product_id=_RNG.choice(catalog_ids),
                            qty=_RNG.randint(1, 4),
                        )
                    )
            if not lines:
                return None
            buyer = _RNG.choice(member_ids) if _RNG.random() < 0.65 else None
            sale = await sales_svc.create_sale(
                store_id, clerk_id, lines=lines, buyer_contact_id=buyer, idempotency_key=key
            )
            sale.created_at = when
            session.add(sale)
            n_sales += 1
            return sale.id

        # 日常銷售（365~21 天前），分批 commit 加速
        BATCH = 100
        for i in range(N_DAILY_SALES):
            await one_sale(_back(365, 21), key=f"big-A-{i}")
            if i % BATCH == BATCH - 1:
                await session.commit()
        await session.commit()

        # 多檔活動：4 檔過去（ENDED）+ 1 檔進行中 + 1 未來草稿 + 1 取消。
        camp_svc = CampaignService(session)
        past = [
            ("開幕回饋 85 折", 15, 340, 320),
            ("週年慶 8 折", 20, 230, 200),
            ("清倉特賣", 30, 150, 120),
            ("揪團露營季", 15, 75, 55),
        ]
        for nm, pct, s_ago, e_ago in past:
            cp = await camp_svc.create_campaign(
                store_id,
                name=nm,
                discount_pct=pct,
                starts_at=_NOW - timedelta(days=s_ago),
                ends_at=_NOW - timedelta(days=e_ago),
                applies_owned_serialized=True,
                applies_owned_bulk=True,
                applies_catalog=True,
                applies_consignment=False,
                created_by=clerk_id,
            )
            # 過去活動：activate→end，成為 ENDED（同時只允許一檔 ACTIVE）。
            await camp_svc.activate(store_id, cp.id, actor_user_id=clerk_id)
            await camp_svc.end(store_id, cp.id, actor_user_id=clerk_id)
            await session.commit()
        future = await camp_svc.create_campaign(
            store_id,
            name="冬季暖帳預告",
            discount_pct=12,
            starts_at=_NOW + timedelta(days=20),
            ends_at=_NOW + timedelta(days=45),
            applies_owned_serialized=True,
            applies_owned_bulk=True,
            applies_catalog=False,
            applies_consignment=False,
            created_by=clerk_id,
        )
        cancelled = await camp_svc.create_campaign(
            store_id,
            name="取消測試活動",
            discount_pct=18,
            starts_at=_NOW + timedelta(days=50),
            ends_at=_NOW + timedelta(days=60),
            applies_owned_serialized=True,
            applies_owned_bulk=False,
            applies_catalog=False,
            applies_consignment=True,
            created_by=clerk_id,
        )
        await camp_svc.cancel(store_id, cancelled.id, actor_user_id=clerk_id)
        await session.commit()
        live = await camp_svc.create_campaign(
            store_id,
            name="春季開幕 9 折",
            discount_pct=10,
            starts_at=_NOW - timedelta(days=21),
            ends_at=_NOW + timedelta(days=10),
            applies_owned_serialized=True,
            applies_owned_bulk=True,
            applies_catalog=True,
            applies_consignment=False,
            created_by=clerk_id,
        )
        await camp_svc.activate(store_id, live.id, actor_user_id=clerk_id)
        await session.commit()
        for i in range(N_LIVE_CAMPAIGN_SALES):
            await one_sale(_back(21, 0), key=f"big-B-{i}")
            if i % BATCH == BATCH - 1:
                await session.commit()
        await session.commit()

        # 作廢一小批（近期）
        recent = (
            (await session.execute(select(Sale).order_by(Sale.id.desc()).limit(40))).scalars().all()
        )
        for recent_sale in recent:
            if _RNG.random() < 0.25:
                got = await sales_svc.get_sale(store_id, recent_sale.id)
                if got is not None:
                    try:
                        await sales_svc.void_sale(got, clerk_id)
                        await session.commit()
                        n_void += 1
                    except Exception:
                        await session.rollback()

        # 購物金折抵：以寄售品付款（活動不折寄售，總額=標價可精準拆收款：現金＋購物金）
        for idx in range(N_STORE_CREDIT_SALES):
            if not sc_reserved:
                break
            cid = _RNG.choice(credited_members)
            bal = await sc.get_balance(store_id, cid)
            if bal <= 0:
                continue
            code = sc_reserved.pop()
            item = await session.scalar(
                select(SerializedItem).where(SerializedItem.item_code == code)
            )
            if item is None:
                continue
            total = int(item.listed_price)
            use = min(int(bal), max(1, total - 1))  # 至少留 1 元現金
            try:
                await sales_svc.create_sale(
                    store_id,
                    clerk_id,
                    lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
                    buyer_contact_id=cid,
                    tenders=[
                        TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal(use)),
                        TenderInput(tender_type=TenderType.CASH, amount=Decimal(total - use)),
                    ],
                    idempotency_key=f"big-sc-{idx}",
                )
                await session.commit()
                n_sc_pay += 1
            except Exception:
                await session.rollback()

        total_members = (
            await session.execute(select(Contact).where(Contact.roles.contains(["MEMBER"])))
        ).scalars()
        n_members = len(list(total_members))
        print(
            f"seed_large 完成：會員={n_members}、寄售人={N_CONSIGNORS}、"
            f"序號品={N_OWNED_ITEMS + N_CONSIGNMENT_ITEMS}"
            f"（自有{N_OWNED_ITEMS}/寄售{N_CONSIGNMENT_ITEMS}）、"
            f"散裝批={N_BULK_LOTS}、數量型={len(catalog_ids)}、菜單={len(menu_ids)}、"
            f"供應商={len(SUPPLIERS)}、採購單={n_po}（收貨{n_recv}）、"
            f"購物金撥入={N_CREDITED_MEMBERS}、"
            f"銷售={n_sales}（作廢{n_void}、含購物金折抵{n_sc_pay}）、"
            f"活動=7（4過去+1進行中+1草稿+1取消；未來草稿 id={future.id}）。"
        )


if __name__ == "__main__":
    asyncio.run(_seed())
