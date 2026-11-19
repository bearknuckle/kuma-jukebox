"""試作ゲームラボのリンクプレビュー画像 lab/og.png を作り直す。

カテゴリは lab/index.html の CATS から自動で読むので、カテゴリを増やしたら実行するだけでよい。
必要なもの: pip install cairosvg fonttools / Noto Sans CJK フォント（fonts-noto-cjk）
使い方: python3 tools/make_og.py
"""
import re
from pathlib import Path

import cairosvg
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab"
FONT_DIR = Path("/usr/share/fonts/opentype/noto")
CHIP_COLORS = ["#7fb2ff", "#ffae73", "#ff8fa3", "#8fe3b0", "#d7a6ff", "#ffe082"]


def jp_font(name):
    col = TTCollection(str(FONT_DIR / name))
    return next(t for t in col.fonts if "JP" in t["name"].getDebugName(1))


def text_path(font, text):
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    pen, x = SVGPathPen(gs), 0
    for ch in text:
        g = cmap[ord(ch)]
        gs[g].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
        x += gs[g].width
    return x, pen.getCommands()


def categories():
    html = (LAB / "index.html").read_text()
    block = re.search(r"const CATS=\[(.*?)\n\];", html, re.S).group(1)
    return re.findall(r"label:'([^']+)'", block)


def main():
    black, bold = jp_font("NotoSansCJK-Black.ttc"), jp_font("NotoSansCJK-Bold.ttc")
    icon = (LAB / "icon.svg").read_text()
    inner = icon.split('viewBox="0 0 512 512">', 1)[1].rsplit("</svg>", 1)[0]
    inner = inner.replace('id="bg"', 'id="ibg"').replace("url(#bg)", "url(#ibg)")
    w1, c1 = text_path(black, "試作ゲームラボ")
    w2, c2 = text_path(bold, "AIと考えた試作ゲームを、ブラウザですぐ遊べる")
    s1, s2 = 560 / w1, min(600 / w2, 0.034)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630">',
        '<defs><linearGradient id="og" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#231f36"/>'
        '<stop offset="1" stop-color="#14121c"/></linearGradient>'
        '<linearGradient id="bar" x1="0" x2="1"><stop offset="0" stop-color="#4f8cff"/>'
        '<stop offset=".55" stop-color="#a06bff"/><stop offset="1" stop-color="#ff8a3d"/></linearGradient></defs>',
        '<rect width="1200" height="630" fill="url(#og)"/>',
        '<rect y="610" width="1200" height="20" fill="url(#bar)"/>',
        f'<g transform="translate(90 135) scale(0.703)">{inner}</g>',
        f'<path transform="translate(530 300) scale({s1:.5f})" fill="#f2eff8" d="{c1}"/>',
        f'<path transform="translate(532 375) scale({s2:.5f})" fill="#b9b2cc" d="{c2}"/>',
    ]
    x, sc = 530, 0.022
    for i, label in enumerate(categories()):
        w, c = text_path(bold, label)
        cw = w * sc + 40
        if x + cw > 1150:
            break
        parts.append(f'<rect x="{x}" y="420" rx="22" width="{cw:.0f}" height="44" fill="#2a2639"/>')
        parts.append(f'<path transform="translate({x + 20:.1f} 451) scale({sc})" '
                     f'fill="{CHIP_COLORS[i % len(CHIP_COLORS)]}" d="{c}"/>')
        x += cw + 14
    parts.append("</svg>")
    cairosvg.svg2png(bytestring="\n".join(parts).encode(), write_to=str(LAB / "og.png"),
                     output_width=1200, output_height=630)
    print("wrote", LAB / "og.png")


if __name__ == "__main__":
    main()
