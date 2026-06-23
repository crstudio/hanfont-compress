"""
汉字字体压缩器 - 命令行入口。

用法:
    python -m hfc.cli --font myfont.ttf --route A --output report.html
    python -m hfc.cli --font myfont.ttf --route B --iou 0.90 --output result.html
    python -m hfc.cli --font myfont.ttf --route C --output report.html
    python -m hfc.cli --demo --route all --output demo.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保能导入同包模块
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ============================================================================
# SVG 渲染器（不依赖 PIL）
# ============================================================================

def glyph_to_svg(glyph_contours, size: int = 80, padding: int = 4) -> str:
    """
    将 GlyphContours 渲染为内联 SVG 字符串。

    Args:
        glyph_contours: GlyphContours 对象
        size: SVG 视口大小
        padding: 内边距
    """
    if not glyph_contours or not glyph_contours.contours:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}"><text x="50%" y="50%" text-anchor="middle" dy=".35em" font-size="{size//2}" fill="#ccc">?</text></svg>'

    # 获取边界框
    all_x, all_y = [], []
    for c in glyph_contours.contours:
        for p in c.points:
            fx, fy = p.x / 64.0, p.y / 64.0
            all_x.append(fx)
            all_y.append(fy)

    if not all_x:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}"></svg>'

    g_min_x, g_max_x = min(all_x), max(all_x)
    g_min_y, g_max_y = min(all_y), max(all_y)
    g_w = g_max_x - g_min_x or 1
    g_h = g_max_y - g_min_y or 1

    # 计算缩放和平移
    avail = size - 2 * padding
    scale = avail / max(g_w, g_h)
    tx = padding + (avail - g_w * scale) / 2 - g_min_x * scale
    ty = padding + (avail - g_h * scale) / 2 - g_min_y * scale

    def t(x: float, y: float) -> tuple[float, float]:
        return (x * scale + tx, y * scale + ty)

    paths = []
    for contour in glyph_contours.contours:
        pts = contour.points
        if len(pts) < 2:
            continue

        # 提取 on-curve 点
        on_pts = []
        off_pts = []
        for p in pts:
            fx, fy = p.x / 64.0, p.y / 64.0
            if p.is_on_curve:
                on_pts.append((fx, fy))
            else:
                off_pts.append((fx, fy))

        if not on_pts:
            continue

        # 构建 SVG 路径（简化：只处理在线上的点）
        d_parts = []
        if on_pts:
            d_parts.append(f"M {on_pts[0][0]*scale+tx:.1f} {on_pts[0][1]*scale+ty:.1f}")
            for px, py in on_pts[1:]:
                d_parts.append(f"L {px*scale+tx:.1f} {py*scale+ty:.1f}")
            d_parts.append("Z")

        paths.append('<path d="' + " ".join(d_parts) + '" fill="#333" stroke="none"/>')

    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
    svg += "".join(paths)
    svg += '</svg>'
    return svg


def glyphs_to_grid_svg(glyph_list, size: int = 60) -> str:
    """将多个字形渲染为网格 SVG。"""
    if not glyph_list:
        return ""

    cols = min(8, len(glyph_list))
    rows = (len(glyph_list) + cols - 1) // cols
    gap = 2
    cell = size
    total_w = cols * cell + (cols - 1) * gap
    total_h = rows * cell + (rows - 1) * gap

    cells = []
    for i, (char, glyph) in enumerate(glyph_list):
        svg = glyph_to_svg(glyph, size=cell)
        col = i % cols
        row = i // cols
        x = col * (cell + gap)
        y = row * (cell + gap)
        cells.append(f'<g transform="translate({x},{y})">{svg}</g>')

    result_svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" width="{total_w}" height="{total_h}">'
    result_svg += "".join(cells)
    result_svg += '</svg>'
    return result_svg


# ============================================================================
# 参数解析
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hfc.cli",
        description="汉字字体压缩器 - 命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m hfc.cli --font myfont.ttf --route A
  python -m hfc.cli --font myfont.ttf --route B --iou 0.90 --output report.html
  python -m hfc.cli --font myfont.ttf --route all --output compare.html
  python -m hfc.cli --demo --route all --output demo.html
        """,
    )

    parser.add_argument("--font", "-f", type=str, default=None, help="输入字体文件路径 (TTF/OTF)")
    parser.add_argument("--demo", action="store_true", help="运行演示模式（使用合成数据）")
    parser.add_argument("--route", "-r", type=str, choices=["A", "B", "C", "all"], default="A",
                        help="压缩路线: A=有损, B=近无损, C=Composite, all=全部 (默认: A)")
    parser.add_argument("--output", "-o", type=str, default=None, help="HTML 报告输出路径")
    parser.add_argument("--iou", type=float, default=0.92, help="IoU 匹配阈值 (默认: 0.92)")
    parser.add_argument("--bitmap", type=int, default=128, help="位图大小 (默认: 128)")
    parser.add_argument("--minsize", type=int, default=200, help="最小部件点数 (默认: 200)")
    parser.add_argument("--delta", type=float, default=0.5, help="Delta 点比例 (路线B, 默认: 0.5)")
    parser.add_argument("--chars", type=str, default=None, help="要处理的字符，如 '一二三' (默认: 全部)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    return parser.parse_args()


# ============================================================================
# 数据加载
# ============================================================================

def _print_progress(current: int, total: int, prefix: str = "", bar_width: int = 30) -> None:
    """打印进度条。"""
    if total <= 0:
        return
    ratio = current / total
    filled = int(bar_width * ratio)
    bar = "█" * filled + "·" * (bar_width - filled)
    pct = f"{ratio * 100:5.1f}%"
    print(f"\r  {prefix}[{bar}] {current}/{total} ({pct})", end="", flush=True)
    if current >= total:
        print()


def load_font_glyphs(font_path: str, chars: str | None = None, verbose: bool = False) -> list:
    """从字体文件加载字形轮廓，分批处理显示进度。"""
    from fontTools.ttLib import TTFont
    from hfc.glyph_extractor import GlyphExtractor

    font = TTFont(font_path)
    cmap = font.getBestCmap()
    if cmap is None:
        raise ValueError("Font has no valid cmap table")
    font.close()

    if chars:
        codepoints = [ord(c) for c in chars]
    else:
        codepoints = [uv for uv in cmap.keys() if 0x4E00 <= uv <= 0x9FFF]

    total = len(codepoints)
    if verbose:
        print(f"  Font: {font_path}")
        print(f"  Total chars: {len(cmap)}")
        print(f"  Chars to process: {total}")

    extractor = GlyphExtractor()

    # 分批处理，每批 100 个
    batch_size = 100
    glyphs = []
    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch_cps = codepoints[batch_start:batch_end]
        results = extractor.extract_batch(font_path, batch_cps)
        for r in results:
            if r.success and r.glyph and r.glyph.contours:
                glyphs.append(r.glyph)
        _print_progress(batch_end, total, prefix="Loading glyphs: ")

    if verbose:
        print(f"  Valid glyphs: {len(glyphs)}")

    return glyphs


def make_demo_glyphs() -> tuple[list, list]:
    """生成演示用的合成字形数据。返回 (glyphs, glyph_dict)。"""
    from hfc.glyph_extractor import GlyphContours, Contour, ContourPoint

    def _make_rect(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def _make_glyph(uv: int, parts: list[list[tuple[int, int]]]) -> GlyphContours:
        contours: list[Contour] = []
        for part in parts:
            pts = [ContourPoint(x=x, y=y, is_on_curve=True) for x, y in part]
            contours.append(Contour(points=pts))
        g = GlyphContours(unicode=uv, contours=contours)
        if contours:
            all_x = [p.x for c in contours for p in c.points]
            all_y = [p.y for c in contours for p in c.points]
            g.bbox = (min(all_x), min(all_y), max(all_x), max(all_y))
        return g

    glyphs = [
        _make_glyph(0x4E00, [_make_rect(0, 0, 500, 500), _make_rect(550, 300, 700, 450)]),
        _make_glyph(0x4E8C, [_make_rect(0, 0, 500, 500), _make_rect(550, 0, 700, 150)]),
        _make_glyph(0x4E09, [_make_rect(100, 100, 250, 250), _make_rect(350, 100, 500, 250)]),
        _make_glyph(0x4E5A, [_make_rect(0, 0, 480, 480)]),
        _make_glyph(0x4E8A, [_make_rect(0, 0, 500, 500), _make_rect(550, 500, 700, 650)]),
        _make_glyph(0x4E94, [_make_rect(0, 200, 800, 280), _make_rect(0, 0, 100, 100)]),
    ]

    # 构建 unicode -> glyph 映射
    glyph_dict = {g.unicode: g for g in glyphs}

    return glyphs, glyph_dict


# ============================================================================
# 各路线执行
# ============================================================================

def run_route_a(glyphs: list, glyph_dict: dict, args: argparse.Namespace) -> dict[str, Any]:
    """执行路线A，返回包含对比信息的完整结果。"""
    from hfc.route_a import RouteAConfig, RouteAEncoder
    from hfc.glyph_extractor import GlyphContours, Contour

    config = RouteAConfig(
        bitmap_size=args.bitmap,
        iou_threshold=args.iou,
        min_component_size=args.minsize,
    )

    t0 = time.time()
    encoder = RouteAEncoder(config)

    def _progress(current: int, total: int) -> None:
        _print_progress(current, total, prefix="Encoding: ")

    result = encoder.encode(glyphs, progress_callback=_progress)
    elapsed = time.time() - t0

    # 解码重建
    decoded = encoder.decode(result)

    # 分类：匹配成功 vs 未匹配
    matched = []
    unmatched = []
    comparisons = []

    for cg in result.compressed_glyphs:
        uv = cg.unicode
        char = chr(uv) if uv < 0x110000 else "?"
        original = glyph_dict.get(uv)

        if cg.mode == "COMPONENT" and cg.components:
            # 重建轮廓
            rec_contours = decoded.get(uv, [])
            rec_glyph = GlyphContours(unicode=uv, contours=rec_contours)
            if rec_contours:
                all_x = [p.x for c in rec_contours for p in c.points]
                all_y = [p.y for c in rec_contours for p in c.points]
                if all_x:
                    rec_glyph.bbox = (min(all_x), min(all_y), max(all_x), max(all_y))

            matched.append({"char": char, "unicode": uv, "mode": "COMPONENT", "score": cg.average_score})
            comparisons.append({
                "char": char,
                "unicode": uv,
                "original": original,
                "reconstructed": rec_glyph,
            })
        else:
            unmatched.append({"char": char, "unicode": uv, "mode": "RAW", "reason": "未匹配到部件"})

    return {
        "route": "A",
        "name": "无 Delta 有损压缩",
        "elapsed_ms": round(elapsed * 1000, 1),
        "config": {
            "bitmap_size": config.bitmap_size,
            "iou_threshold": config.iou_threshold,
            "min_component_size": config.min_component_size,
        },
        "stats": result.stats,
        "summary": result.summary(),
        "matched": matched,
        "unmatched": unmatched,
        "comparisons": comparisons,
    }


def run_route_b(glyphs: list, glyph_dict: dict, args: argparse.Namespace) -> dict[str, Any]:
    """执行路线B。"""
    from hfc.route_b import RouteBConfig, RouteBEncoder
    from hfc.glyph_extractor import GlyphContours

    config = RouteBConfig(
        bitmap_size=args.bitmap,
        iou_threshold=args.iou,
        min_component_size=args.minsize,
        delta_point_ratio=args.delta,
    )

    t0 = time.time()
    encoder = RouteBEncoder(config)

    def _progress(current: int, total: int) -> None:
        _print_progress(current, total, prefix="Encoding: ")

    result = encoder.encode(glyphs, progress_callback=_progress)
    elapsed = time.time() - t0

    # 解码（RouteBEncoder 继承自 RouteAEncoder，decode 方法相同）
    decoded = encoder.decode(result)

    matched = []
    unmatched = []
    comparisons = []

    for cg in result.compressed_glyphs:
        uv = cg.unicode
        char = chr(uv) if uv < 0x110000 else "?"
        original = glyph_dict.get(uv)

        if cg.mode == "COMPONENT" and cg.components:
            rec_contours = decoded.get(uv, [])
            rec_glyph = GlyphContours(unicode=uv, contours=rec_contours)
            if rec_contours:
                all_x = [p.x for c in rec_contours for p in c.points]
                all_y = [p.y for c in rec_contours for p in c.points]
                if all_x:
                    rec_glyph.bbox = (min(all_x), min(all_y), max(all_x), max(all_y))

            matched.append({"char": char, "unicode": uv, "mode": "COMPONENT", "score": cg.average_score})
            comparisons.append({
                "char": char,
                "unicode": uv,
                "original": original,
                "reconstructed": rec_glyph,
            })
        else:
            unmatched.append({"char": char, "unicode": uv, "mode": "RAW", "reason": "未匹配或Delta不划算"})

    return {
        "route": "B",
        "name": "带 Delta 近无损压缩",
        "elapsed_ms": round(elapsed * 1000, 1),
        "config": {
            "bitmap_size": config.bitmap_size,
            "iou_threshold": config.iou_threshold,
            "min_component_size": config.min_component_size,
            "delta_point_ratio": config.delta_point_ratio,
        },
        "stats": result.stats,
        "summary": result.summary(),
        "matched": matched,
        "unmatched": unmatched,
        "comparisons": comparisons,
    }


def run_route_c(font_path: str, glyphs: list, glyph_dict: dict, args: argparse.Namespace) -> dict[str, Any]:
    """执行路线C。"""
    from hfc.composite_encoder import RouteCConfig, RouteCEncoder

    config = RouteCConfig(
        reuse_threshold=args.iou,
        min_component_size=args.minsize,
    )

    output_ttf = Path(args.output or "output").with_suffix(".ttf")
    output_ttf = output_ttf.with_name(output_ttf.stem + "_routeC.ttf")

    t0 = time.time()
    encoder = RouteCEncoder(config)
    result = encoder.encode(font_path, glyphs, output_path=str(output_ttf))
    elapsed = time.time() - t0

    return {
        "route": "C",
        "name": "TrueType Composite Glyph",
        "elapsed_ms": round(elapsed * 1000, 1),
        "config": {
            "reuse_threshold": config.reuse_threshold,
            "min_component_size": config.min_component_size,
        },
        "stats": {
            "original_font_size": result.original_font_size,
            "new_ttf_size": result.new_ttf_size,
            "component_glyphs_created": result.component_glyphs_created,
            "chars_recomposed": result.chars_recomposed,
            "chars_raw_fallback": result.chars_raw_fallback,
            "compression_ratio": (
                1 - result.new_ttf_size / result.original_font_size
                if result.original_font_size > 0 else 0
            ),
        },
        "output_path": result.output_path,
        "summary": f"原字体: {result.original_font_size} bytes, 新字体: {result.new_ttf_size} bytes, "
                   f"部件数: {result.component_glyphs_created}, composite字: {result.chars_recomposed}",
        "matched": [],
        "unmatched": [],
        "comparisons": [],
    }


# ============================================================================
# HTML 报告生成
# ============================================================================

def svg_to_data_uri(svg_str: str) -> str:
    """将 SVG 转为 data URI。"""
    b64 = base64.b64encode(svg_str.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def build_comparison_cell(item: dict, size: int = 80) -> str:
    """构建单个字符的对比单元格（原始 vs 重建并排）。"""
    char = item["char"]
    original = item.get("original")
    reconstructed = item.get("reconstructed")

    orig_svg = glyph_to_svg(original, size=size) if original else ""
    reco_svg = glyph_to_svg(reconstructed, size=size) if reconstructed else ""

    # 两个 SVG 并排
    cell_w = size * 2 + 8
    combined = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cell_w} {size}" width="{cell_w}" height="{size}">'
    combined += f'<rect width="{cell_w}" height="{size}" fill="#f0f0f0"/>'
    if orig_svg:
        # 去掉外层 svg 标签
        inner = orig_svg.replace('<svg xmlns="http://www.w3.org/2000/svg"', '').replace('</svg>', '')
        combined += f'<g transform="translate(0,0)">{inner}</g>'
    if reco_svg:
        inner = reco_svg.replace('<svg xmlns="http://www.w3.org/2000/svg"', '').replace('</svg>', '')
        combined += f'<g transform="translate({size + 4},0)">{inner}</g>'
    combined += f'<text x="{size//2}" y="{size + 12}" text-anchor="middle" font-size="10" fill="#666">Orig</text>'
    combined += f'<text x="{size + 4 + size//2}" y="{size + 12}" text-anchor="middle" font-size="10" fill="#666">Reco</text>'
    combined += '</svg>'

    data_uri = svg_to_data_uri(combined)
    return f'''
    <div class="char-cell">
      <div class="char-label">{char}</div>
      <div class="char-code">U+{item["unicode"]:04X}</div>
      <img src="{data_uri}" alt="{char}" title="{char}"/>
    </div>'''


def build_char_grid(items: list, size: int = 60) -> str:
    """构建字符网格 SVG。"""
    if not items:
        return '<div class="empty-note">None</div>'

    cells = []
    for item in items:
        glyph = item.get("original") or item.get("reconstructed")
        char = item["char"]
        svg = glyph_to_svg(glyph, size=size)
        cells.append((char, svg))

    cols = min(8, len(cells))
    rows = (len(cells) + cols - 1) // cols
    gap = 2
    cell = size
    total_w = cols * cell + (cols - 1) * gap
    total_h = rows * cell + (rows - 1) * gap + 16

    cell_svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" width="{total_w}" height="{total_h}">'
    cell_svg += '<rect width="100%" height="100%" fill="#fafafa"/>'

    for i, (char, svg) in enumerate(cells):
        col = i % cols
        row = i // cols
        x = col * (cell + gap)
        y = row * (cell + gap)
        inner = svg.replace('<svg xmlns="http://www.w3.org/2000/svg"', '').replace('</svg>', '')
        cell_svg += f'<g transform="translate({x},{y})">{inner}</g>'
        cell_svg += f'<text x="{x + cell//2}" y="{y + cell + 12}" text-anchor="middle" font-size="10" fill="#666">{char}</text>'

    cell_svg += '</svg>'
    return f'<div class="char-grid">{cell_svg}</div>'


def generate_html_report(results: list[dict[str, Any]], font_path: str, output_path: str) -> None:
    """生成带 Tabs 的 HTML 报告。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构建每个路线的 Tab 内容
    tab_contents = ""
    tab_links = ""

    for i, r in enumerate(results):
        route_id = f"route{r['route']}"
        is_active = "active" if i == 0 else ""

        tab_links += f'''
        <button class="tab-btn {is_active}" onclick="openTab('{route_id}')">
            Route-{r['route']}: {r['name']}
        </button>'''

        # 配置表格
        config_rows = ""
        if r.get("config"):
            for k, v in r["config"].items():
                config_rows += f"<tr><th>{k}</th><td>{v}</td></tr>"

        # 统计表格
        stats_rows = ""
        if r.get("stats"):
            for k, v in r["stats"].items():
                if isinstance(v, float):
                    v = f"{v:.4f}"
                stats_rows += f"<tr><th>{k}</th><td>{v}</td></tr>"

        # 匹配成功字符
        matched_html = ""
        if r.get("matched"):
            for item in r["matched"]:
                matched_html += build_comparison_cell(item, size=70)
        else:
            matched_html = '<div class="empty-note">No matched characters</div>'

        # 未匹配字符网格
        unmatched_html = ""
        if r.get("unmatched"):
            unmatched_html = build_char_grid(r["unmatched"], size=50)
        else:
            unmatched_html = '<div class="empty-note">All matched</div>'

        tab_contents += f'''
        <div id="{route_id}" class="tab-content {'active' if i == 0 else ''}">
            <div class="route-header">
                <h2>Route-{r['route']}: {r['name']}</h2>
                <span class="elapsed">Time: {r['elapsed_ms']} ms</span>
            </div>

            <div class="info-panels">
                <div class="panel">
                    <h3>Config</h3>
                    <table class="info-table">
                        {config_rows or '<tr><td colspan="2">-</td></tr>'}
                    </table>
                </div>
                <div class="panel">
                    <h3>Statistics</h3>
                    <table class="info-table">
                        {stats_rows or '<tr><td colspan="2">-</td></tr>'}
                    </table>
                </div>
            </div>

            <div class="section">
                <h3>Matched Characters (Compression -> Decompression)</h3>
                <p class="section-desc">Left: Original | Right: Reconstructed</p>
                <div class="comparison-grid">
                    {matched_html}
                </div>
            </div>

            <div class="section">
                <h3>Unmatched / RAW Mode Characters</h3>
                <div class="unmatched-grid">
                    {unmatched_html}
                </div>
            </div>
        </div>'''

    # 汇总信息
    summary_rows = ""
    for r in results:
        stats = r.get("stats", {})
        ratio = stats.get("estimated_compression_ratio", stats.get("compression_ratio", "N/A"))
        if isinstance(ratio, float):
            ratio = f"{ratio:.2%}"
        summary_rows += f"<tr><td>Route-{r['route']}</td><td>{r['name']}</td>"
        summary_rows += f"<td>{r['elapsed_ms']} ms</td>"
        summary_rows += f"<td>{len(r.get('matched', []))}</td>"
        summary_rows += f"<td>{len(r.get('unmatched', []))}</td>"
        summary_rows += f"<td>{ratio}</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>HanFont Compression Report</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f5f5;
      color: #333;
      line-height: 1.6;
      padding: 20px;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 8px;
                 box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 30px; }}
    h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-bottom: 20px; }}
    .meta {{ color: #666; margin-bottom: 20px; padding: 15px; background: #f8f9fa; border-radius: 4px; }}
    .meta div {{ margin: 4px 0; }}

    /* Tabs */
    .tab-nav {{ display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 20px; gap: 4px; }}
    .tab-btn {{ padding: 10px 20px; border: none; background: #e8e8e8; cursor: pointer;
                border-radius: 6px 6px 0 0; font-size: 14px; transition: background 0.2s; }}
    .tab-btn:hover {{ background: #d0d0d0; }}
    .tab-btn.active {{ background: #3498db; color: #fff; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}

    .route-header {{ display: flex; align-items: center; gap: 20px; margin-bottom: 20px; }}
    .route-header h2 {{ color: #2980b9; }}
    .elapsed {{ color: #e74c3c; font-weight: bold; }}

    .info-panels {{ display: flex; gap: 20px; margin-bottom: 24px; }}
    .panel {{ flex: 1; background: #f8f9fa; padding: 15px; border-radius: 6px; }}
    .panel h3 {{ color: #555; font-size: 14px; margin-bottom: 10px; border-bottom: 1px solid #ddd; padding-bottom: 6px; }}
    .info-table {{ width: 100%; border-collapse: collapse; }}
    .info-table th, .info-table td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; }}
    .info-table th {{ color: #888; font-weight: normal; width: 45%; }}
    .info-table td {{ font-family: Consolas, monospace; }}

    .section {{ margin-bottom: 30px; }}
    .section h3 {{ color: #333; margin-bottom: 8px; }}
    .section-desc {{ color: #888; font-size: 13px; margin-bottom: 12px; }}

    /* 字符对比网格 */
    .comparison-grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .char-cell {{ text-align: center; background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px;
                  padding: 6px; min-width: 90px; }}
    .char-cell img {{ width: 100%; height: auto; display: block; }}
    .char-label {{ font-size: 18px; font-weight: bold; color: #333; }}
    .char-code {{ font-size: 10px; color: #999; font-family: monospace; }}

    /* 未匹配网格 */
    .unmatched-grid {{ background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 10px; }}
    .char-grid {{ overflow-x: auto; }}
    .empty-note {{ color: #999; font-style: italic; padding: 20px; text-align: center; }}

    /* 汇总表 */
    .summary-table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    .summary-table th, .summary-table td {{ padding: 10px; border: 1px solid #ddd; text-align: center; }}
    .summary-table th {{ background: #f0f0f0; color: #555; }}
    .summary-table tr:hover {{ background: #fafafa; }}

    .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; color: #999; font-size: 12px; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>HanFont Compression Report</h1>
    <div class="meta">
      <div><strong>Font:</strong> {font_path}</div>
      <div><strong>Generated:</strong> {timestamp}</div>
      <div><strong>Routes:</strong> {', '.join(r['route'] + ':' + r['name'] for r in results)}</div>
    </div>

    <div class="tab-nav">{tab_links}</div>
    <div class="tab-contents">{tab_contents}</div>

    <h2 style="margin-top:30px;">Summary</h2>
    <table class="summary-table">
      <tr>
        <th>Route</th><th>Name</th><th>Time</th><th>Matched</th><th>Unmatched</th><th>Est. Ratio</th>
      </tr>
      {summary_rows}
    </table>

    <div class="footer">Generated by hanfont_compress</div>
  </div>

  <script>
    function openTab(id) {{
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      event.target.classList.add('active');
    }}
  </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nHTML 报告已生成: {output_path}")


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    args = parse_args()

    if args.demo:
        font_path = "demo (合成数据)"
        print("=" * 60)
        print("汉字字体压缩器 - 演示模式")
        print("=" * 60)
        glyphs, glyph_dict = make_demo_glyphs()
        print(f"\n[生成演示数据] {len(glyphs)} 个合成字形")
    else:
        if not args.font:
            print("错误: 请指定字体文件 --font 或使用 --demo 模式")
            sys.exit(1)
        if not os.path.exists(args.font):
            print(f"错误: 字体文件不存在: {args.font}")
            sys.exit(1)

        print("=" * 60)
        print("汉字字体压缩器")
        print("=" * 60)
        print(f"\n[加载字体] {args.font}")
        glyphs = load_font_glyphs(args.font, args.chars, args.verbose)
        glyph_dict = {g.unicode: g for g in glyphs}
        font_path = args.font

    if not glyphs:
        print("错误: 没有找到有效的汉字字形")
        sys.exit(1)

    print(f"[有效字形数] {len(glyphs)}")

    results: list[dict[str, Any]] = []

    if args.route in ("A", "all"):
        print("\n[路线A] 无 Delta 有损压缩 ...")
        res = run_route_a(glyphs, glyph_dict, args)
        results.append(res)
        print(f"  匹配成功: {len(res['matched'])} 字符")
        print(f"  未匹配: {len(res['unmatched'])} 字符")
        if args.verbose:
            print(res["summary"])

    if args.route in ("B", "all"):
        print("\n[路线B] 带 Delta 近无损压缩 ...")
        res = run_route_b(glyphs, glyph_dict, args)
        results.append(res)
        print(f"  匹配成功: {len(res['matched'])} 字符")
        print(f"  未匹配: {len(res['unmatched'])} 字符")
        if args.verbose:
            print(res["summary"])

    if args.route in ("C", "all"):
        print("\n[路线C] TrueType Composite Glyph ...")
        if args.demo:
            print("  (演示模式跳过路线C)")
        else:
            try:
                res = run_route_c(args.font, glyphs, glyph_dict, args)
                results.append(res)
                if args.verbose:
                    print(res["summary"])
            except Exception as e:
                print(f"  警告: 路线C 执行失败: {e}")

    # 生成报告
    output_path = args.output
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"report_{timestamp}.html"

    generate_html_report(results, font_path, output_path)

    print("\n" + "=" * 60)
    print("执行完成")
    print("=" * 60)
    for r in results:
        print(f"路线{r['route']}: {r['elapsed_ms']} ms | 匹配 {len(r.get('matched', []))} / 未匹配 {len(r.get('unmatched', []))}")

    abs_path = str(Path(output_path).resolve())
    print(f"\n报告文件: {abs_path}")


if __name__ == "__main__":
    main()
