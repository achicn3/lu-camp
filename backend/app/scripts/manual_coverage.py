"""產生 `docs/manual/coverage.yaml`（docs/37 P1 盤點）。

**用產生的、不用手寫的**：手寫清單一定會與程式碼脫節，而脫節的盤點比沒有盤點更糟——
它會讓人以為已經涵蓋。本腳本從三個來源交叉比對：

1. 前端路由（`frontend/app/**/page.tsx`）與選單（`(authed)/layout.tsx`）
2. 後端端點與權限（各 `router.py` 的 `@router.*` 與依賴別名）
3. 手冊腳本實際造訪的路由（`frontend/scripts/manual/*.mjs`）

執行：

    cd backend && uv run python -m app.scripts.manual_coverage
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND = _ROOT / "frontend"
_BACKEND = _ROOT / "backend"

# 依賴別名 → 權限層級。**別名有好幾個**（AuthDep/StaffDep/CurrentUserDep 都是「登入即可」），
# 只認其中一個會把另外兩種誤判成「未驗證」——第一版就是這樣多報了 7 個假的公開端點。
_LOGIN_DEPS = ("CurrentUserDep", "AuthDep", "StaffDep")
_KIOSK_DEPS = ("KioskPrincipalDep", "KioskMutationDep")


@dataclass
class Endpoint:
    module: str
    method: str
    path: str
    auth: str


@dataclass
class Screen:
    route: str
    label: str = ""
    in_menu: bool = False
    manager_only: bool = False
    scripts: list[str] = field(default_factory=list)


def _menu() -> dict[str, tuple[str, bool]]:
    src = (_FRONTEND / "app/(authed)/layout.tsx").read_text()
    out: dict[str, tuple[str, bool]] = {}
    for m in re.finditer(
        r'\{\s*href:\s*"([^"]+)",\s*label:\s*"([^"]+)"(.*?)\}', src, re.S
    ):
        out[m.group(1)] = (m.group(2), "managerOnly: true" in m.group(3))
    return out


def _routes() -> list[str]:
    out = []
    for p in sorted((_FRONTEND / "app").rglob("page.tsx")):
        rel = p.relative_to(_FRONTEND / "app").parent.as_posix()
        rel = re.sub(r"\(authed\)/?", "", rel)
        out.append("/" + ("" if rel == "." else rel))
    return sorted(set(out))


def _endpoints() -> list[Endpoint]:
    rows: list[Endpoint] = []
    for f in sorted((_BACKEND / "app/modules").rglob("router.py")):
        src = f.read_text()
        prefix = ""
        head = re.search(r"APIRouter\((.*?)\)", src, re.S)
        if head:
            pm = re.search(r'prefix\s*=\s*"([^"]*)"', head.group(1))
            if pm:
                prefix = pm.group(1)
        for block in re.split(r"\n@router\.", src)[1:]:
            mm = re.match(r'(get|post|patch|put|delete)\(\s*\n?\s*"([^"]*)"', block)
            if not mm:
                continue
            # 只看函式簽名（到 `) ->` 為止），不要掃進函式本體——本體裡的字串會誤判
            dm = re.search(r"\n(?:async )?def \w+\((.*?)\n\) ->", block, re.S)
            sig = dm.group(1) if dm else block[:900]
            if "ManagerDep" in sig:
                auth = "MANAGER"
            elif any(k in sig for k in _KIOSK_DEPS):
                auth = "顧客螢幕裝置"
            elif any(k in sig for k in _LOGIN_DEPS):
                auth = "登入即可"
            else:
                auth = "未驗證"
            rows.append(
                Endpoint(f.parent.name, mm.group(1).upper(), (prefix + mm.group(2)) or prefix, auth)
            )
    return rows


def _script_routes() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in sorted((_FRONTEND / "scripts/manual").glob("[0-9]*.mjs")):
        found = set()
        for m in re.finditer(r"\$\{BASE\}(/[a-z-]*)", f.read_text()):
            found.add(m.group(1) or "/")
        for route in found:
            out.setdefault(route, []).append(f.name)
    return out


def _report_tabs() -> tuple[list[str], int | None]:
    """報表頁的分頁清單，以及手冊腳本自稱涵蓋幾個。

    **分頁是最容易漏的東西**：新增一個分頁不會新增路由，路由層級的盤點看不出來。
    實測 `14-reports.mjs` 開頭自稱「12 個分頁」，但頁面已有 15 個。
    """
    src = (_FRONTEND / "app/(authed)/reports/page.tsx").read_text()
    block = re.search(r"const TABS[^=]*=\s*\[(.*?)\n\];", src, re.S)
    tabs = re.findall(r'label:\s*"([^"]+)"', block.group(1)) if block else []
    claimed = None
    script = _FRONTEND / "scripts/manual/14-reports.mjs"
    if script.exists():
        # **只看標題那一行**：整份檔案掃的話，說明段落裡提到的數字也會被當成宣稱值
        # （本檔的說明就寫著「標題寫『12 個分頁』」，於是永遠報 stale——實測踩過）。
        first_line = script.read_text().splitlines()[0] if script.read_text() else ""
        m = re.search(r"(\d+)\s*個分頁", first_line)
        if m:
            claimed = int(m.group(1))
    return tabs, claimed


def build() -> str:
    menu = _menu()
    covered = _script_routes()
    screens = []
    for route in _routes():
        label, manager_only = menu.get(route, ("", False))
        screens.append(
            Screen(
                route=route,
                label=label,
                in_menu=route in menu,
                manager_only=manager_only,
                scripts=sorted(covered.get(route, [])),
            )
        )
    endpoints = _endpoints()

    lines = [
        "# 手冊涵蓋度盤點（docs/37 P1）",
        "#",
        "# **本檔由 `backend/app/scripts/manual_coverage.py` 產生，不要手改。**",
        "# 手寫清單一定會與程式碼脫節，而脫節的盤點比沒有盤點更糟——它會讓人以為已經涵蓋。",
        "",
        "screens:",
    ]
    for s in screens:
        lines += [
            f'  - route: "{s.route}"',
            f'    label: "{s.label}"' if s.label else '    label: ""',
            f"    in_menu: {str(s.in_menu).lower()}",
            f"    manager_only: {str(s.manager_only).lower()}",
        ]
        if s.scripts:
            lines.append("    scripts: [" + ", ".join(f'"{x}"' for x in s.scripts) + "]")
        else:
            lines.append("    scripts: []          # ← 手冊未涵蓋")
    gaps = [s.route for s in screens if not s.scripts]
    by_auth: dict[str, int] = {}
    for e in endpoints:
        by_auth[e.auth] = by_auth.get(e.auth, 0) + 1

    lines += ["", "endpoints:", f"  total: {len(endpoints)}", "  by_auth:"]
    lines += [f"    {k}: {v}" for k, v in sorted(by_auth.items(), key=lambda kv: -kv[1])]
    lines += ["  unauthenticated:"]
    for e in endpoints:
        if e.auth == "未驗證":
            lines.append(f'    - "{e.method} {e.path}"   # {e.module}')
    tabs, claimed = _report_tabs()
    lines += ["", "report_tabs:", f"  total: {len(tabs)}"]
    if claimed is not None:
        lines.append(f"  claimed_by_14_reports: {claimed}")
    lines += ["  list:"] + [f'    - "{t}"' for t in tabs]

    lines += ["", "gaps:", "  screens_without_script:"]
    lines += [f'    - "{g}"' for g in gaps] or ["    []"]
    if claimed is not None and claimed != len(tabs):
        lines += [
            "  report_tabs_stale:",
            f'    - "14-reports.mjs 自稱涵蓋 {claimed} 個分頁，實際有 {len(tabs)} 個"',
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    out = _ROOT / "docs/manual/coverage.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"已產生 {out}")


if __name__ == "__main__":
    main()
