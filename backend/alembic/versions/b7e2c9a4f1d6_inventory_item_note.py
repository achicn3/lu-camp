"""add note column to the three inventory entities

Revision ID: b7e2c9a4f1d6
Revises: c4e8a2b6d9f1
Create Date: 2026-09-02 10:00:00.000000

商品備註（2026-09-02 裁示）：單一自由欄位，兼「商品狀況說明」與「內部作業備忘」。
三種庫存型態（序號單品／一般商品／散裝批）一律都加，避免「查得到 A 查不到 B」。
Additive、nullable、無 backfill：既有列一律 NULL＝沒有備註。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9a4f1d6"
down_revision: str | Sequence[str] | None = "c4e8a2b6d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("serialized_items", "catalog_products", "bulk_lots")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        op.add_column(table, sa.Column("note", sa.String(length=500), nullable=True))


def abort_if_notes_exist(bind: sa.engine.Connection) -> None:
    """有任何非空備註就拒絕降版（fail closed）。

    備註是店員手打的、無處可還原：DROP 掉之後就算再 upgrade 回來也只剩空欄位，
    「缺充電線」「先別賣」這些交貨前必須看到的事會靜默消失。回滾程式碼不必然要
    回滾 schema——這個欄位是 additive，舊版程式碼在有它的資料庫上照樣跑。
    """
    # 先鎖後數（同一交易內）：只 SELECT 的話，計數完成到 DROP COLUMN 之間仍可能有
    # PATCH 寫入新備註並提交，ALTER 等它結束後照樣刪掉——fail-closed 就形同虛設。
    # ACCESS EXCLUSIVE 與 DROP COLUMN 需要的鎖相同，等於把檢查與刪除變成一個原子動作。
    for table in _TABLES:
        # 表名來自本檔常數 _TABLES，非外部輸入。
        bind.execute(sa.text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
    counts = {
        table: bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE note IS NOT NULL")
        ).scalar_one()
        for table in _TABLES
    }
    populated = {table: n for table, n in counts.items() if n}
    if populated:
        detail = "、".join(f"{table} {n} 筆" for table, n in populated.items())
        raise RuntimeError(
            f"拒絕降版：已有商品備註（{detail}），DROP 之後無從還原。"
            "本欄位為 additive，舊版程式碼可直接在含此欄位的資料庫上執行——"
            "請只回滾程式碼、不要降 schema；確定要丟棄請自備備份後手動處理。"
        )


def downgrade() -> None:
    """Downgrade schema."""
    abort_if_notes_exist(op.get_bind())
    for table in reversed(_TABLES):
        op.drop_column(table, "note")
