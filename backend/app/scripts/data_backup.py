"""每日資料備份／還原（邏輯備份，CSV＋JSON，可隨時無損匯入）。

設計目標（使用者裁示）：
- 每天把整個資料庫**每張表**匯出成 **CSV ＋ JSON 兩種**，放到「以日期命名的資料夾」，
  方便之後**定期 rsync/複製到其他硬碟**。
- 這些檔案**之後可以隨時匯入回來、且不會格式亂掉**：以 **JSON 為無損還原來源**
  （金額用字串、時間用 ISO、bytes 用 base64、JSONB 原樣保留），CSV 為人工檢視／試算表副本。
- **不修改任何現有 DB model**：純讀 `Base.metadata` 的表結構做泛型 dump/load。

與 `pg_dump` 的取捨：本店單機、要的是「跨工具可讀、可挑表、可塞進試算表」的可攜備份，
故採邏輯備份。要做整機 binary 備份仍可另行 `pg_dump`，兩者不衝突。

格式（輸出目錄結構）::

    <out>/<YYYY-MM-DD_HHMMSS>/
        manifest.json          # 版本、alembic head、各表列數、時間
        <table>.json           # 無損還原來源（每表一檔，rows 陣列）
        <table>.csv            # 人工檢視副本（每表一檔）

還原一律讀 JSON（無損）。還原前會比對 manifest 的 alembic 版本與當前 DB 是否一致，
不一致則拒絕（避免 schema 對不上造成「格式亂掉」），除非帶 --force。

執行::

    cd backend
    # 備份（預設輸出到 ./backups；可用 BACKUP_DIR 或 --out 覆寫）
    uv run python -m app.scripts.data_backup backup --out ./backups
    # 還原（指定某次備份資料夾；--truncate 先清空各表再灌）
    uv run python -m app.scripts.data_backup restore --in ./backups/2026-06-22_0300 --truncate
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Date, DateTime, LargeBinary, Numeric, Table, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

import app.main  # noqa: F401  # 觸發所有模型註冊，讓 Base.metadata 完整
from app.core.db import Base, get_engine

FORMAT_VERSION = 1
_BYTES_TAG = "__bytes_b64__"

# 表名 → 該表所有列（每列為 欄位名→JSON-able 值）。
type TableRows = dict[str, list[dict[str, Any]]]


# ── 序列化（DB 值 ↔ JSON-able）─────────────────────────────────────────────────


def encode_value(value: Any) -> Any:
    """把單一欄位值轉成可無損寫入 JSON 的型別。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)  # 金額/數值一律字串，避免 float 失真
    if isinstance(value, datetime):  # 須在 date 之前（datetime 是 date 子類）
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES_TAG: base64.b64encode(bytes(value)).decode("ascii")}
    return value  # int / str / bool / dict / list（含 JSONB）原樣


def decode_value(column_type: Any, raw: Any) -> Any:
    """依欄位 SQL 型別把 JSON 值還原成 DB 可寫入的 Python 值（無損）。"""
    if raw is None:
        return None
    if isinstance(column_type, Numeric):
        return Decimal(str(raw))
    if isinstance(column_type, DateTime):  # 須在 Date 之前
        return datetime.fromisoformat(raw)
    if isinstance(column_type, Date):
        return date.fromisoformat(raw)
    if isinstance(column_type, LargeBinary):
        token = raw[_BYTES_TAG] if isinstance(raw, Mapping) else raw
        return base64.b64decode(token)
    return raw  # String / Integer / Boolean / Enum(VARCHAR) / JSONB 原樣


def _csv_cell(value: Any) -> str:
    """CSV 人工檢視用：None→空、dict/list→JSON 文字、其餘→str。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _ordered_tables() -> list[Table]:
    """FK 依賴排序（父表在前）；備份用順序，還原時沿用、清空時反向。"""
    return list(Base.metadata.sorted_tables)


# ── 備份：DB 讀取（async）與檔案寫出（sync）分離 ───────────────────────────────


async def _alembic_revision(conn: AsyncConnection) -> str | None:
    """讀目前 alembic 版本；無 alembic_version 表時回 None（不丟例外、不汙染交易）。"""
    if await conn.scalar(text("SELECT to_regclass('alembic_version')")) is None:
        return None
    revision: str | None = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return revision


async def dump_database(conn: AsyncConnection) -> tuple[TableRows, str | None]:
    """純讀 DB：回傳 {表名: JSON-able rows} 與 alembic 版本（不碰檔案）。"""
    tables: TableRows = {}
    for table in _ordered_tables():
        result = await conn.execute(select(table))
        columns = list(result.keys())
        tables[table.name] = [
            {col: encode_value(val) for col, val in zip(columns, row, strict=True)}
            for row in result.fetchall()
        ]
    return tables, await _alembic_revision(conn)


def write_backup(
    tables: Mapping[str, list[dict[str, Any]]],
    alembic_revision: str | None,
    out_dir: Path,
) -> dict[str, Any]:
    """把 dump 結果寫成每表 .json + .csv 與 manifest.json；回傳 manifest。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in _ordered_tables():
        rows = tables.get(table.name, [])
        columns = [c.name for c in table.columns]
        (out_dir / f"{table.name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with (out_dir / f"{table.name}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_csv_cell(row.get(c)) for c in columns])
        counts[table.name] = len(rows)

    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "alembic_revision": alembic_revision,
        "tables": counts,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


async def backup_database(conn: AsyncConnection, out_dir: Path) -> dict[str, Any]:
    """備份便利函式：讀 DB → 寫檔，回傳 manifest。"""
    tables, revision = await dump_database(conn)
    return write_backup(tables, revision, out_dir)


# ── 還原：檔案讀取（sync）與 DB 寫入（async）分離 ───────────────────────────────


class RestoreError(RuntimeError):
    """還原前置條件不符（schema 版本不符、目標表非空等）。"""


def load_backup(in_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """讀某次備份資料夾：回傳 (manifest, {表名: 原始 JSON rows})。"""
    manifest = json.loads((in_dir / "manifest.json").read_text(encoding="utf-8"))
    tables: TableRows = {}
    for table in _ordered_tables():
        path = in_dir / f"{table.name}.json"
        tables[table.name] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        )
    return manifest, tables


async def _reset_sequence(conn: AsyncConnection, table: Table) -> None:
    """把整數主鍵的 sequence 推進到目前最大值，避免之後新增撞 PK。"""
    for col in table.primary_key.columns:
        if isinstance(col.type, Numeric) or col.type.python_type is not int:
            continue
        max_expr = f"(SELECT COALESCE(MAX({col.name}), 0) FROM {table.name})"
        has_rows = f"(SELECT COUNT(*) FROM {table.name}) > 0"
        await conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence(:tbl, :col), {max_expr}, {has_rows})"
            ).bindparams(tbl=table.name, col=col.name)
        )


async def restore_database(
    conn: AsyncConnection,
    in_dir: Path,
    *,
    truncate: bool = False,
    force: bool = False,
) -> dict[str, int]:
    """從 in_dir 的 JSON 無損還原。回傳每表插入列數。

    - schema 版本（alembic）與當前 DB 不符 → RestoreError（除非 force）。
    - 目標表非空且未指定 truncate → RestoreError（避免覆蓋既有資料）。
    """
    manifest, raw_tables = load_backup(in_dir)
    current_rev = await _alembic_revision(conn)
    backup_rev = manifest.get("alembic_revision")
    if not force and backup_rev != current_rev:
        raise RestoreError(
            f"schema 版本不符：備份={backup_rev} 目前={current_rev}；"
            "請先 alembic upgrade 到相同版本，或用 --force（風險自負）。"
        )

    tables = _ordered_tables()

    if not truncate:
        for table in tables:
            if (await conn.execute(select(table).limit(1))).first() is not None:
                raise RestoreError(
                    f"資料表 {table.name} 非空；還原請加 --truncate（會先清空所有表）。"
                )
    else:
        names = ", ".join(t.name for t in reversed(tables))
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

    inserted: dict[str, int] = {}
    for table in tables:
        raw_rows = raw_tables.get(table.name, [])
        if not raw_rows:
            inserted[table.name] = 0
            continue
        coltypes = {c.name: c.type for c in table.columns}
        records = [
            {name: decode_value(coltypes[name], val) for name, val in row.items()}
            for row in raw_rows
        ]
        await conn.execute(table.insert(), records)
        await _reset_sequence(conn, table)
        inserted[table.name] = len(records)

    return inserted


# ── CLI ───────────────────────────────────────────────────────────────────────


async def _run_backup(out_root: Path) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    out_dir = out_root / stamp
    async with get_engine().begin() as conn:
        manifest = await backup_database(conn, out_dir)
    total = sum(manifest["tables"].values())
    print(f"備份完成：{out_dir}（{len(manifest['tables'])} 表 / {total} 列）")


async def _run_restore(in_dir: Path, *, truncate: bool, force: bool) -> None:
    async with get_engine().begin() as conn:
        inserted = await restore_database(conn, in_dir, truncate=truncate, force=force)
    total = sum(inserted.values())
    n_tables = len([n for n, c in inserted.items() if c])
    print(f"還原完成：{in_dir}（{n_tables} 表 / {total} 列）")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="資料備份／還原（CSV＋JSON 邏輯備份）")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backup", help="匯出所有表到日期資料夾（CSV＋JSON）")
    b.add_argument("--out", default=os.environ.get("BACKUP_DIR", "./backups"))

    r = sub.add_parser("restore", help="從某次備份資料夾無損還原（讀 JSON）")
    r.add_argument("--in", dest="in_dir", required=True)
    r.add_argument("--truncate", action="store_true", help="還原前先清空所有表")
    r.add_argument("--force", action="store_true", help="略過 schema 版本檢查（風險自負）")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "backup":
        asyncio.run(_run_backup(Path(args.out)))
    else:
        asyncio.run(_run_restore(Path(args.in_dir), truncate=args.truncate, force=args.force))


if __name__ == "__main__":
    main()
