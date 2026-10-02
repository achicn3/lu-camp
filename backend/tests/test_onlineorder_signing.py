"""店內 backend → 雲端 Worker 的請求簽章（docs/44 §5.2）。

跨語言向量：同一組數字也寫在 online-order/test/signature-vector.test.ts；
兩邊任何一邊改了簽章規則，兩邊測試都會紅。
"""

from app.modules.onlineorder.signing import canonical_string, sign_request

VECTOR_SECRET = "vector-secret"
VECTOR_SIGNATURE = "c4b01a296615d5083c69d9cb172099a0b773225f5f7d5d98fe2d2778c7e3a373"


def test_signature_matches_worker_vector() -> None:
    headers = sign_request(
        VECTOR_SECRET,
        "PUT",
        "/integration/menu",
        b'{"version":1}',
        timestamp=1790900000,
        nonce="0123456789abcdef-vector",
    )
    assert headers == {
        "X-LuCamp-Timestamp": "1790900000",
        "X-LuCamp-Nonce": "0123456789abcdef-vector",
        "X-LuCamp-Signature": VECTOR_SIGNATURE,
    }


def test_canonical_string_includes_body_hash_not_body() -> None:
    text = canonical_string("PUT", "/integration/menu", "1", "n" * 16, b"secret body")
    assert "secret body" not in text
    assert text.split("\n")[:4] == ["PUT", "/integration/menu", "1", "n" * 16]


def test_default_nonce_is_unique_and_worker_compatible() -> None:
    a = sign_request("s", "PUT", "/x", b"")["X-LuCamp-Nonce"]
    b = sign_request("s", "PUT", "/x", b"")["X-LuCamp-Nonce"]
    assert a != b
    # Worker 只收 16–64 個英數或連字號
    assert 16 <= len(a) <= 64 and all(c.isalnum() or c == "-" for c in a)
