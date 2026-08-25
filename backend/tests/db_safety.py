"""Derive a process-isolated PostgreSQL database before application modules create engines."""

import os
import re

from sqlalchemy.engine import make_url

from app.core.config import get_settings

BASE_DATABASE_URL = get_settings().database_url
_base_url = make_url(BASE_DATABASE_URL)
_base_name = re.sub(r"[^a-zA-Z0-9_]", "_", _base_url.database or "lucamp")[:40]
TEST_DATABASE_NAME = f"{_base_name}_test_{os.getpid()}"
if not re.fullmatch(r"[a-zA-Z0-9_]+_test_[0-9]+", TEST_DATABASE_NAME):
    raise RuntimeError("拒絕建立名稱不安全的測試資料庫")

os.environ["DATABASE_URL"] = _base_url.set(database=TEST_DATABASE_NAME).render_as_string(
    hide_password=False
)
get_settings.cache_clear()
