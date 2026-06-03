"""core/crypto.py — PII 加密與 national_id blind-index。"""

import os

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import (
    PiiCipher,
    compute_blind_index,
    get_pii_cipher,
    national_id_blind_index,
)


def _key() -> bytes:
    return os.urandom(32)


def test_encrypt_decrypt_roundtrip() -> None:
    cipher = PiiCipher(_key())
    token = cipher.encrypt("A123456789")
    assert token != "A123456789"  # 密文不等於明文
    assert cipher.decrypt(token) == "A123456789"


def test_encrypt_is_non_deterministic() -> None:
    # 隨機 nonce → 同明文兩次加密產生不同密文，無法以等值比對搜尋。
    cipher = PiiCipher(_key())
    assert cipher.encrypt("A123456789") != cipher.encrypt("A123456789")


def test_decrypt_with_wrong_key_fails() -> None:
    token = PiiCipher(_key()).encrypt("A123456789")
    with pytest.raises(InvalidTag):
        PiiCipher(_key()).decrypt(token)


def test_blind_index_same_value_same_index() -> None:
    key = b"hmac-key"
    assert compute_blind_index("A123456789", key) == compute_blind_index("A123456789", key)


def test_blind_index_different_value_different_index() -> None:
    key = b"hmac-key"
    assert compute_blind_index("A123456789", key) != compute_blind_index("B987654321", key)


def test_factories_use_config_keys() -> None:
    cipher = get_pii_cipher()
    assert cipher.decrypt(cipher.encrypt("A123456789")) == "A123456789"
    idx = national_id_blind_index("A123456789")
    assert idx == national_id_blind_index("A123456789")  # 確定性
    assert len(idx) == 64  # sha256 hex


async def test_stored_value_is_ciphertext_not_plaintext_searchable(
    db_session: AsyncSession,
) -> None:
    cipher = get_pii_cipher()
    plaintext = "A123456789"
    token = cipher.encrypt(plaintext)

    await db_session.execute(text("CREATE TEMP TABLE pii_probe (national_id_enc text)"))
    await db_session.execute(
        text("INSERT INTO pii_probe (national_id_enc) VALUES (:t)"), {"t": token}
    )

    # 以明文搜尋找不到（密文不可當明文比對）。
    found = (
        await db_session.execute(
            text("SELECT count(*) FROM pii_probe WHERE national_id_enc = :p"),
            {"p": plaintext},
        )
    ).scalar_one()
    assert found == 0

    # 實際儲存的是密文，且可解回原值。
    stored = (await db_session.execute(text("SELECT national_id_enc FROM pii_probe"))).scalar_one()
    assert stored != plaintext
    assert cipher.decrypt(stored) == plaintext
