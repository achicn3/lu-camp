"""跨模組共用列舉。"""

from enum import StrEnum


class UserRole(StrEnum):
    """使用者角色。MANAGER 為管理者（可跨店/解密 PII），CLERK 為門市店員。"""

    MANAGER = "MANAGER"
    CLERK = "CLERK"


class ContactRole(StrEnum):
    """聯絡人角色（統一主檔可同時具備多重角色）。"""

    MEMBER = "MEMBER"
    SELLER = "SELLER"
    CONSIGNOR = "CONSIGNOR"


class Grade(StrEnum):
    """成色分級。S-D 走序號單品（serialized_item），E 為散裝批（bulk_lot）。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class OwnershipType(StrEnum):
    """序號品擁有型態。OWNED=買斷，CONSIGNMENT=寄售。"""

    OWNED = "OWNED"
    CONSIGNMENT = "CONSIGNMENT"


class SerializedItemStatus(StrEnum):
    """序號品狀態機。"""

    IN_STOCK = "IN_STOCK"
    SOLD = "SOLD"
    RETURNED_TO_CONSIGNOR = "RETURNED_TO_CONSIGNOR"
    WRITTEN_OFF = "WRITTEN_OFF"


class BulkLotStatus(StrEnum):
    """散裝批狀態。"""

    ON_SALE = "ON_SALE"
    SOLD_OUT = "SOLD_OUT"
    WRITTEN_OFF = "WRITTEN_OFF"


class BulkAcquisitionBasis(StrEnum):
    """散裝批收購計價基礎。"""

    WEIGHT = "WEIGHT"
    BAG = "BAG"
    UNSPECIFIED = "UNSPECIFIED"


class CashSessionStatus(StrEnum):
    """現金抽屜班別狀態。"""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CashMovementType(StrEnum):
    """現金異動類型。

    SALE_IN 進帳；BUYOUT_OUT / CONSIGNMENT_PAYOUT_OUT 出帳；MANUAL_ADJUST 可正可負。
    """

    SALE_IN = "SALE_IN"
    BUYOUT_OUT = "BUYOUT_OUT"
    CONSIGNMENT_PAYOUT_OUT = "CONSIGNMENT_PAYOUT_OUT"
    MANUAL_ADJUST = "MANUAL_ADJUST"


class AcquisitionType(StrEnum):
    """收購/寄售入庫單類型。

    BUYOUT 買斷（建序號品、付現）；CONSIGNMENT 寄售（建序號品、不付現）；
    BULK_LOT E 級散裝（建散裝批、付現）。
    """

    BUYOUT = "BUYOUT"
    CONSIGNMENT = "CONSIGNMENT"
    BULK_LOT = "BULK_LOT"


class ItemKind(StrEnum):
    """庫存品種類（stock_movement 用）。"""

    SERIALIZED = "SERIALIZED"
    CATALOG = "CATALOG"
    BULK_LOT = "BULK_LOT"


class StockDirection(StrEnum):
    """庫存異動方向。"""

    IN = "IN"
    OUT = "OUT"
    ADJUST = "ADJUST"


class StockReason(StrEnum):
    """庫存異動原因。"""

    ACQUISITION = "ACQUISITION"
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    CONSIGN_RETURN = "CONSIGN_RETURN"
    WRITE_OFF = "WRITE_OFF"
    STOCKTAKE = "STOCKTAKE"
