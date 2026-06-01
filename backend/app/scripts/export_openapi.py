"""把後端 OpenAPI 規格輸出為 JSON（合約管線唯一事實來源，docs/11）。

用法（從 backend/ 執行）：
    uv run python -m app.scripts.export_openapi

直接寫入 `<repo>/frontend/openapi.json`（UTF-8、LF、`sort_keys=True`），
讓合約漂移檢查（git diff）不受平台 stdout 編碼、字典順序或換行差異影響。
"""

import json
from pathlib import Path

from app.main import create_app

# backend/app/scripts/export_openapi.py -> repo root = parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_PATH = REPO_ROOT / "frontend" / "openapi.json"


def main() -> None:
    spec = create_app().openapi()
    text = json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    OUTPUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
