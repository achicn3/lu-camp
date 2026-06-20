#!/usr/bin/env bash
# 一次性安裝瀏覽器 E2E（Playwright）在無 root WSL 環境所需的執行庫與 CJK 字型。
# 冪等：已安裝就跳過。安裝到使用者家目錄（持久），不需 sudo。
#
# 用法：
#   source frontend/scripts/setup-browser-e2e.sh      # 會 export LU_CAMP_PW_LDPATH
#   LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH" node frontend/scripts/consignment-smoke.mjs
#
# 背景：headless chromium 需 libnspr4/libnss3/… 等系統庫；本機無 sudo 無法 apt install，
# 故以 `apt-get download` 取 .deb、`dpkg -x` 解到家目錄，再用 LD_LIBRARY_PATH 指向。
# 另裝 WenQuanYi 正黑，否則截圖中文會變方框（cosmetic）。
set -euo pipefail

PW_LIBDIR="${LU_CAMP_PW_LIBDIR:-$HOME/.cache/lu-camp-e2e/pwlibs}"
FONT_DIR="$HOME/.local/share/fonts"
LDPATH="$PW_LIBDIR/usr/lib/x86_64-linux-gnu:$PW_LIBDIR/lib/x86_64-linux-gnu"

# chromium 執行庫名稱（noble/24.04）。libnssutil3/libsmime3 包含在 libnss3 內。
PKGS="libnspr4 libnss3 libasound2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 \
libatspi2.0-0t64 libcups2t64 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
libxkbcommon0 libgbm1 libpango-1.0-0 libcairo2 libxcb1 libdrm2 libexpat1 libglib2.0-0t64"

if [ -f "$PW_LIBDIR/usr/lib/x86_64-linux-gnu/libnspr4.so" ]; then
  echo "✅ chromium 執行庫已就緒：$PW_LIBDIR"
else
  echo "→ 下載並解開 chromium 執行庫到 $PW_LIBDIR …"
  tmp="$(mktemp -d)"
  mkdir -p "$PW_LIBDIR"
  ( cd "$tmp" && for p in $PKGS; do apt-get download "$p" 2>/dev/null || echo "  (略過 $p)"; done )
  for deb in "$tmp"/*.deb; do dpkg -x "$deb" "$PW_LIBDIR" 2>/dev/null || true; done
  rm -rf "$tmp"
  [ -f "$PW_LIBDIR/usr/lib/x86_64-linux-gnu/libnspr4.so" ] || { echo "❌ libnspr4 取得失敗"; return 1 2>/dev/null || exit 1; }
  echo "✅ chromium 執行庫安裝完成"
fi

if fc-list 2>/dev/null | grep -qi "wenquanyi\|zenhei"; then
  echo "✅ CJK 字型已就緒（WenQuanYi）"
else
  echo "→ 安裝 WenQuanYi 正黑 CJK 字型 …"
  mkdir -p "$FONT_DIR"
  tmp="$(mktemp -d)"
  ( cd "$tmp" && apt-get download fonts-wqy-zenhei 2>/dev/null || true )
  for deb in "$tmp"/*.deb; do dpkg -x "$deb" "$tmp/x" 2>/dev/null || true; done
  find "$tmp/x" \( -name "*.ttc" -o -name "*.ttf" \) -exec cp {} "$FONT_DIR/" \; 2>/dev/null || true
  rm -rf "$tmp"
  fc-cache -f "$FONT_DIR" >/dev/null 2>&1 || true
  fc-list | grep -qi "wenquanyi\|zenhei" && echo "✅ CJK 字型安裝完成" || echo "⚠ 字型安裝可能失敗（截圖中文恐顯示方框）"
fi

export LU_CAMP_PW_LDPATH="$LDPATH"
echo ""
echo "下一步：export LD_LIBRARY_PATH=\"\$LU_CAMP_PW_LDPATH:\$LD_LIBRARY_PATH\" 後即可跑 playwright 煙霧測試。"
