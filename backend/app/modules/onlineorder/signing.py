"""店內 backend → 雲端 Worker 的請求簽章（docs/44 §5.2）。

簽章字串＝METHOD \\n PATH(含 query) \\n TIMESTAMP \\n NONCE \\n hex(SHA-256(body))；
簽章＝hex(HMAC-SHA256(secret, 簽章字串))。Worker 端同一套規則在 online-order/src/auth.ts，
兩邊用同一組跨語言測試向量守住。
"""

import hashlib
import hmac
import time
import uuid


def canonical_string(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method, path, timestamp, nonce, hashlib.sha256(body).hexdigest()])


def sign_request(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """回傳要加在請求上的三個標頭；nonce 預設每次新產生（Worker 會拒收重複的 nonce）。"""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    n = nonce if nonce is not None else str(uuid.uuid4())
    text = canonical_string(method, path, ts, n, body)
    signature = hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()
    return {"X-LuCamp-Timestamp": ts, "X-LuCamp-Nonce": n, "X-LuCamp-Signature": signature}
