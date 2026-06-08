"""ESC/POS 列印與錢櫃控制（Phase 0 骨架）。

以 `SupportsWrite` 介面包裝實體印表機，測試可用 `FakePrinter` 驗證送出的
位元組序列，不需實機。商品條碼採 **Code 128**（識別碼：序號品 item_code /
散裝堆 lot_code）。

注意：此處的 ESC/POS 位元組為最小代表性實作；接上實機時，須依該印表機型號的
指令手冊校正（CLAUDE.md：不得憑記憶硬寫硬體欄位）。
"""

from typing import Protocol

ESC = b"\x1b"
GS = b"\x1d"
FS = b"\x1c"


class SupportsWrite(Protocol):
    """可寫入位元組的印表機介面。"""

    def write(self, data: bytes) -> None: ...


class FakePrinter:
    """測試用假印表機：累積寫入的位元組以供斷言。"""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)


def open_drawer(printer: SupportsWrite) -> None:
    """送出錢櫃 kick 指令（ESC p m t1 t2）。"""
    printer.write(ESC + b"p" + bytes([0, 25, 250]))


def encode_code128(code: str) -> bytes:
    """把識別碼編成 Code 128 條碼列印指令（GS k 73 n d1..dn）。"""
    data = code.encode("ascii")
    return GS + b"k" + bytes([73, len(data)]) + data


def print_label(printer: SupportsWrite, code: str, name: str, price: int) -> None:
    """列印商品標籤：可讀文字（品名、價格）+ Code 128 條碼。"""
    printer.write(f"{name}\n".encode())
    printer.write(f"NT${price}\n".encode())
    printer.write(encode_code128(code))
    printer.write(b"\n")
