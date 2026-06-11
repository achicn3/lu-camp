"""密碼雜湊（core/security）單元測試：argon2、驗證、鹽隨機。"""

from app.core.security import hash_password, verify_password


def test_hash_is_argon2_and_not_plaintext() -> None:
    hashed = hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert hashed.startswith("$argon2")  # CLAUDE.md §5：argon2/bcrypt 雜湊


def test_verify_roundtrip() -> None:
    hashed = hash_password("s3cret-pw")
    assert verify_password("s3cret-pw", hashed) is True


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("s3cret-pw")
    assert verify_password("wrong-pw", hashed) is False


def test_salts_are_random() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_malformed_hash() -> None:
    """資料庫雜湊損壞時如實回 False，不丟例外到認證流程。"""
    assert verify_password("whatever", "not-a-real-hash") is False
