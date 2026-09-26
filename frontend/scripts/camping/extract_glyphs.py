"""把顧客螢幕露營動畫要用的幾個字，從開源字型取出輪廓，寫成 SVG path 常數。

只取用得到的字（露坑、謝謝光臨…），不必讓平板下載整套 5MB 的中文字型，也不怕機器上缺字型。
字型皆為 SIL Open Font License 1.1（可嵌入、可衍生），來源：
  Long Cang（馬克筆手寫）https://github.com/google/fonts/tree/main/ofl/longcang
  Huninn 粉圓（圓體）    https://github.com/google/fonts/tree/main/ofl/huninn

用法（字型檔自行下載到同一資料夾）：
  uv run --with fonttools python scripts/camping/extract_glyphs.py <字型資料夾>
輸出：features/customer-display/camping/glyphs.ts
"""

import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONTS = {
    "marker": ("LongCang-Regular.ttf", "露坑"),
    "round": ("Huninn-Regular.ttf", "露坑謝光臨歡迎雲海營地咖啡"),
}
OUT = Path(__file__).resolve().parents[2] / "features/customer-display/camping/glyphs.ts"


def glyph_paths(font_path: Path, chars: str) -> dict[str, tuple[str, int]]:
    font = TTFont(font_path)
    scale = 1000 / font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    result: dict[str, tuple[str, int]] = {}
    for ch in dict.fromkeys(chars):
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyph_set, ntos=lambda v: f"{v:.0f}")
        # 字型座標 y 向上；SVG y 向下，基線放在 y=0、字高約 -880。
        glyph_set[name].draw(TransformPen(pen, (scale, 0, 0, -scale, 0, 0)))
        result[ch] = (pen.getCommands(), round(glyph_set[name].width * scale))
    return result


def main() -> None:
    font_dir = Path(sys.argv[1])
    lines = [
        "// 由 scripts/camping/extract_glyphs.py 產生，勿手改。",
        "// 字形輪廓取自 Long Cang、Huninn 粉圓（SIL Open Font License 1.1）。",
        "// 單位：1em = 1000，基線 y=0（字身往上是負值）。",
        "",
        "export type Glyph = { d: string; adv: number };",
        "",
    ]
    for key, (file, chars) in FONTS.items():
        lines.append(f"export const {key.upper()}_GLYPHS: Record<string, Glyph> = {{")
        for ch, (d, adv) in glyph_paths(font_dir / file, chars).items():
            lines.append(f'  "{ch}": {{ adv: {adv}, d: "{d}" }},')
        lines.append("};")
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
