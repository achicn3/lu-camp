"""PII 欄位加密與 national_id blind-index。

- 加密：AES-256-GCM 認證式加密；每次以隨機 nonce，故同一明文每次密文不同、無法等值搜尋。
- blind-index：HMAC-SHA256，確定性，僅供精確去重比對（不可反推明文）。
- 金鑰一律由外部注入（建構式 / 參數），邏輯不寫死金鑰；正式用的工廠由 config 取金鑰，
  日後輪替只需更換環境金鑰、不必改動本模組。
"""

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_NONCE_BYTES = 12


class PiiCipher:
    """AES-256-GCM 認證式加密；金鑰由建構式注入。"""

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        """加密並回傳 base64(nonce || ciphertext) 字串。"""
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()

    def decrypt(self, token: str) -> str:
        """還原明文；密文遭竄改或金鑰錯誤會擲出 InvalidTag。"""
        raw = base64.b64decode(token)
        nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return self._aead.decrypt(nonce, ciphertext, None).decode()


def compute_blind_index(value: str, key: bytes) -> str:
    """HMAC-SHA256(value, key) 的 hex；同值同金鑰→同結果，供精確去重比對。"""
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def get_pii_cipher() -> PiiCipher:
    """以設定中的 PII 金鑰（base64 of 32 bytes）建立 PiiCipher。"""
    return PiiCipher(base64.b64decode(get_settings().pii_enc_key))


def national_id_blind_index(national_id: str) -> str:
    """以設定中的 HMAC 金鑰計算 national_id 的 blind index。"""
    return compute_blind_index(national_id, get_settings().hmac_key.encode())
