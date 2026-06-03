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
