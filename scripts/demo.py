"""
演示脚本: 汉字字体压缩完整流水线

使用方法:
    # 1. 用内置的合成字形库演示 (不需要任何字体文件)
    python scripts/demo.py

    # 2. 指定一个真实 TTF/OTF 字体文件
    python scripts/demo.py --font path/to/font.ttf

    # 3. 自定义输出路径
    python scripts/demo.py --font font.ttf --output output.hfc --max-chars 500

流水线:
    字体文件 → 字形轮廓提取 → 部件库初始化 → 部件匹配编码
    → .hfc 序列化 → 解码还原 → 统计与验证
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 确保可以 import src/ 目录下的模块
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from hfc.component_library import (
    Component,
    ComponentLibrary,
    ComponentLibraryConfig,
    ComponentLibraryInitializer,
)
from hfc.component_matcher import (
    ComponentMatcher,
    EncodedChar,
    MatchConfig,
)
from hfc.glyph_extractor import (
    Contour,
    ContourPoint,
    GlyphContours,
    GlyphExtractor,
)
from hfc.hfc_encoder import HFCEncoder, EncodeOptions
from hfc.human_reviewer import HumanReviewer, ReviewSession
from hfc.renderer import GlyphRenderer


# ============================================================================
# 辅助: 合成测试字形（当用户没有提供字体文件时使用）
# ============================================================================

def _make_square(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: 正方形轮廓"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    c.add_point(0, 0, True)
    c.add_point(size, 0, True)
    c.add_point(size, size, True)
    c.add_point(0, size, True)
    return g


def _make_triangle(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: 三角形轮廓"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    c.add_point(0, 0, True)
    c.add_point(size, 0, True)
    c.add_point(size // 2, size, True)
    return g


def _make_diamond(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: 菱形轮廓"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    half = size // 2
    c.add_point(half, 0, True)
    c.add_point(size, half, True)
    c.add_point(half, size, True)
    c.add_point(0, half, True)
    return g


def _make_pentagon(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: 五边形"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    cx, cy = size / 2, size / 2
    r = size / 2
    for i in range(5):
        angle = -math.pi / 2 + i * 2 * math.pi / 5
        x = int(cx + r * math.cos(angle))
        y = int(cy + r * math.sin(angle))
        c.add_point(x, y, True)
    return g


def _make_hexagon(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: 六边形"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    cx, cy = size / 2, size / 2
    r = size / 2
    for i in range(6):
        angle = -math.pi / 2 + i * 2 * math.pi / 6
        x = int(cx + r * math.cos(angle))
        y = int(cy + r * math.sin(angle))
        c.add_point(x, y, True)
    return g


def _make_l_shaped(unicode_val: int, size: int = 20000) -> GlyphContours:
    """合成: L形（两个矩形合成的 L）"""
    g = GlyphContours(unicode=unicode_val, bbox=(0, 0, size, size))
    c = g.add_contour()
    half = size // 2
    c.add_point(0, 0, True)
    c.add_point(size, 0, True)
    c.add_point(size, half, True)
    c.add_point(half, half, True)
    c.add_point(half, size, True)
    c.add_point(0, size, True)
    return g


def build_synthetic_library() -> tuple[ComponentLibrary, list[GlyphContours]]:
    """
    构建一个合成的演示库。

    Returns:
        (component_library, glyphs_list)
        - component_library: 3 个部件（正方形、三角形、菱形）
        - glyphs_list: 一批用于编码的字形（包含部件形状的变体）
    """
    # -------- 部件库 --------
    library = ComponentLibrary()

    comp_sq = Component(id="shape_square", name="正方形", semantic="口")
    comp_sq.add_sample_from_glyph(_make_square(0x0001, size=20000))
    library.add_component(comp_sq)

    comp_tri = Component(id="shape_triangle", name="三角形", semantic="△")
    comp_tri.add_sample_from_glyph(_make_triangle(0x0002, size=20000))
    library.add_component(comp_tri)

    comp_dia = Component(id="shape_diamond", name="菱形", semantic="◇")
    comp_dia.add_sample_from_glyph(_make_diamond(0x0003, size=20000))
    library.add_component(comp_dia)

    comp_pent = Component(id="shape_pentagon", name="五边形", semantic="⬟")
    comp_pent.add_sample_from_glyph(_make_pentagon(0x0004, size=20000))
    library.add_component(comp_pent)

    comp_hex = Component(id="shape_hexagon", name="六边形", semantic="⬢")
    comp_hex.add_sample_from_glyph(_make_hexagon(0x0005, size=20000))
    library.add_component(comp_hex)

    # -------- 待编码的字形（混合了部件形状 + 随机尺寸 + 扰动）--------
    glyphs: list[GlyphContours] = []
    shape_makers = [
        _make_square,
        _make_triangle,
        _make_diamond,
        _make_pentagon,
        _make_hexagon,
        _make_l_shaped,  # 第6种形状：库中没有的 → 预期走 RAW 模式
    ]

    random.seed(42)  # 可重复
    total_per_shape = 8
    for shape_idx, maker in enumerate(shape_makers):
        base_unicode = 0x4E00 + shape_idx * total_per_shape
        for i in range(total_per_shape):
            uv = base_unicode + i
            # 引入一些尺寸变化 + 小扰动
            size = random.randint(14000, 22000)
            glyph = maker(uv, size=size)
            glyphs.append(glyph)

    # 打乱顺序，模拟真实情况
    random.shuffle(glyphs)

    return library, glyphs


# ============================================================================
# 统计信息
# ============================================================================

@dataclass
class PipelineStats:
    """流水线运行统计"""

    total_glyphs: int = 0
    component_mode_count: int = 0
    raw_mode_count: int = 0
    match_scores: list[float] = field(default_factory=list)
    enc_size: int = 0
    dec_size: int = 0
    time_extract: float = 0.0
    time_match: float = 0.0
    time_encode: float = 0.0
    time_decode: float = 0.0

    def print(self) -> None:
        print()
        print("=" * 60)
        print("  流水线统计")
        print("=" * 60)
        print(f"  字形总数:       {self.total_glyphs}")
        print(f"  部件编码模式:   {self.component_mode_count} "
              f"({self.component_mode_count / max(self.total_glyphs, 1) * 100:.1f}%)")
        print(f"  RAW编码模式:    {self.raw_mode_count} "
              f"({self.raw_mode_count / max(self.total_glyphs, 1) * 100:.1f}%)")

        if self.match_scores:
            avg_score = sum(self.match_scores) / len(self.match_scores)
            max_score = max(self.match_scores)
            min_score = min(self.match_scores)
            print(f"  匹配得分:       平均 {avg_score:.3f} "
                  f"最高 {max_score:.3f} 最低 {min_score:.3f}")

        print()
        print(f"  提取耗时:       {self.time_extract * 1000:.1f} ms")
        print(f"  匹配耗时:       {self.time_match * 1000:.1f} ms")
        print(f"  编码耗时:       {self.time_encode * 1000:.1f} ms")
        print(f"  解码耗时:       {self.time_decode * 1000:.1f} ms")
        total_t = self.time_extract + self.time_match + self.time_encode + self.time_decode
        print(f"  总耗时:         {total_t * 1000:.1f} ms")
        print("=" * 60)


# ============================================================================
# 主流水线
# ============================================================================

def run_synthetic_demo(output_path: Path, max_chars: int = 0) -> PipelineStats:
    """
    模式1: 用合成数据跑完整流水线

    Args:
        output_path: .hfc 输出路径
        max_chars: 最大字形数（0 = 不限）
    """
    print("=" * 60)
    print("  HanFont Compress - 合成数据演示")
    print("=" * 60)

    stats = PipelineStats()

    # -------- 1. 合成部件库 + 字形 --------
    print("\n[1/5] 构建合成部件库 + 字形 ...")
    t0 = time.time()

    library, all_glyphs = build_synthetic_library()
    if max_chars > 0 and max_chars < len(all_glyphs):
        all_glyphs = all_glyphs[:max_chars]

    stats.total_glyphs = len(all_glyphs)
    stats.time_extract = time.time() - t0
    print(f"  ✓ 部件库: {len(library)} 个部件")
    print(f"  ✓ 字形: {len(all_glyphs)} 个")

    # 打印部件名
    for comp in library:
        samples = comp.num_samples()
        print(f"      - {comp.name} ({comp.id}): {samples} 样本")

    # -------- 2. 匹配编码 --------
    print("\n[2/5] 部件匹配编码 ...")
    t0 = time.time()

    match_config = MatchConfig(
        similarity_threshold=0.50,  # 合成数据较简单，放宽阈值
    )
    matcher = ComponentMatcher(library, config=match_config)

    encoded_chars: list[EncodedChar] = []
    for g in all_glyphs:
        enc = matcher.match(g)
        encoded_chars.append(enc)

    stats.time_match = time.time() - t0

    stats.component_mode_count = sum(
        1 for e in encoded_chars if e.mode == "COMPONENT"
    )
    stats.raw_mode_count = sum(1 for e in encoded_chars if e.mode == "RAW")
    stats.match_scores = [
        e.match_score for e in encoded_chars if e.mode == "COMPONENT"
    ]
    print(f"  ✓ {stats.component_mode_count} 个字形 → 部件编码模式")
    print(f"  ✓ {stats.raw_mode_count} 个字形 → RAW 编码模式")

    # 打印前 5 个示例
    for enc in encoded_chars[:5]:
        parts_str = ", ".join(
            p.component_id for p in enc.parts
        ) if enc.parts else "RAW"
        print(f"      - U+{enc.unicode:04X}: mode={enc.mode} "
              f"score={enc.match_score:.3f} parts=[{parts_str}]")

    # -------- 3. 编码到 .hfc 文件 --------
    print("\n[3/5] 编码为 .hfc 文件 ...")
    t0 = time.time()

    encoder = HFCEncoder()
    encode_options = EncodeOptions(
        use_brotli=True,
        include_component_samples=True,
    )

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    encode_result = encoder.encode_to_file(
        library, encoded_chars, str(output_path),
        options=encode_options,
    )
    stats.time_encode = time.time() - t0
    stats.enc_size = encode_result.bytes_written

    print(f"  ✓ 写入: {output_path}")
    print(f"  ✓ 大小: {encode_result.bytes_written:,} bytes "
          f"(压缩率 ~{100 - encode_result.bytes_written / max(encode_result.uncompressed_size, 1) * 100:.1f}%)")
    print(f"  ✓ 方法: {encode_result.compression_method}")

    # -------- 4. 解码还原 --------
    print("\n[4/5] 解码 .hfc 文件 ...")
    t0 = time.time()

    decoded = encoder.decode_from_file(str(output_path))
    stats.time_decode = time.time() - t0
    stats.dec_size = encode_result.bytes_written

    # 验证解码回来的字符数是否一致
    assert len(decoded.chars) == len(encoded_chars), (
        f"解码字符数不符: 期望 {len(encoded_chars)}, 得到 {len(decoded.chars)}"
    )
    assert len(decoded.components) == len(library), (
        f"解码部件数不符: 期望 {len(library)}, 得到 {len(decoded.components)}"
    )
    print(f"  ✓ 解码 {len(decoded.chars)} 个字形, {len(decoded.components)} 个部件")

    # 模式统计对比
    dec_comp = sum(1 for c in decoded.chars if c.mode == "COMPONENT")
    dec_raw = sum(1 for c in decoded.chars if c.mode == "RAW")
    print(f"      - COMPONENT 模式: {dec_comp}")
    print(f"      - RAW 模式: {dec_raw}")

    # -------- 5. 渲染小预览 --------
    print("\n[5/5] 生成渲染预览图 ...")
    try:
        renderer = GlyphRenderer()
        # 选取前 6 个 COMPONENT 模式字形做渲染
        component_glyphs = [
            (f"U+{e.unicode:04X}", g)
            for e, g in zip(encoded_chars, all_glyphs)
            if e.mode == "COMPONENT"
        ][:6]

        preview_path = output_path.parent / "preview.png"
        if component_glyphs:
            result = renderer.render_grid(
                component_glyphs, cols=3,
            )
            result.image.save(str(preview_path))
            print(f"  ✓ 预览图: {preview_path}")
        else:
            print("  - (无 COMPONENT 模式字形, 跳过渲染)")
    except Exception as ex:
        print(f"  - (渲染跳过: {ex})")

    # 打印统计
    stats.print()
    return stats


def run_font_demo(
    font_path: Path,
    output_path: Path,
    max_chars: int = 50,
) -> PipelineStats:
    """
    模式2: 从真实 TTF/OTF 字体跑流水线

    Args:
        font_path: TTF/OTF 文件路径
        output_path: .hfc 输出路径
        max_chars: 最多处理多少汉字
    """
    print("=" * 60)
    print(f"  HanFont Compress - 真实字体演示")
    print(f"  字体: {font_path.name}")
    print("=" * 60)

    stats = PipelineStats()

    # -------- 1. 提取字形 --------
    print("\n[1/5] 从字体提取字形 ...")
    t0 = time.time()

    extractor = GlyphExtractor()
    all_glyphs: list[GlyphContours] = []
    for result in extractor.extract_all_cjk(str(font_path)):
        if not result.success:
            continue
        if result.glyph.is_empty():
            continue
        all_glyphs.append(result.glyph)
        if max_chars > 0 and len(all_glyphs) >= max_chars:
            break

    stats.total_glyphs = len(all_glyphs)
    stats.time_extract = time.time() - t0

    if not all_glyphs:
        print("  ✗ 未从字体提取到任何 CJK 字形")
        return stats

    print(f"  ✓ 提取了 {len(all_glyphs)} 个汉字字形")
    for g in all_glyphs[:5]:
        c = chr(g.unicode) if 0 < g.unicode < 0x110000 else "?"
        print(f"      - U+{g.unicode:04X} ({c})")

    # -------- 2. 构建部件库 --------
    # 使用字形的前 N 个作为"部件样本"（演示用法，
    # 实际项目中应该用部首表 / 结构分析结果）
    print("\n[2/5] 构建部件库 ...")
    library = ComponentLibrary()

    # 以字形的前若干个作为"伪部件"（演示流程，实际应使用部首表）
    N = min(10, len(all_glyphs))
    for i, g in enumerate(all_glyphs[:N]):
        c = chr(g.unicode) if 0 < g.unicode < 0x110000 else "?"
        comp = Component(
            id=f"demo_radical_{i:02d}",
            name=f"部件_{c}",
            semantic=c,
        )
        comp.add_sample_from_glyph(g)
        library.add_component(comp)

    print(f"  ✓ 部件库: {len(library)} 个部件")

    # -------- 3. 匹配编码 --------
    print("\n[3/5] 部件匹配编码 ...")
    t0 = time.time()

    match_config = MatchConfig(
        similarity_threshold=0.60,
    )
    matcher = ComponentMatcher(library, config=match_config)

    encoded_chars: list[EncodedChar] = []
    for g in all_glyphs:
        encoded_chars.append(matcher.match(g))

    stats.time_match = time.time() - t0
    stats.component_mode_count = sum(
        1 for e in encoded_chars if e.mode == "COMPONENT"
    )
    stats.raw_mode_count = sum(1 for e in encoded_chars if e.mode == "RAW")
    stats.match_scores = [
        e.match_score for e in encoded_chars if e.mode == "COMPONENT"
    ]
    print(f"  ✓ {stats.component_mode_count} 个字形 → 部件编码")
    print(f"  ✓ {stats.raw_mode_count} 个字形 → RAW 编码")

    # -------- 4. 编码到 .hfc --------
    print("\n[4/5] 编码为 .hfc 文件 ...")
    t0 = time.time()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoder = HFCEncoder()
    options = EncodeOptions(use_brotli=True, include_component_samples=True)
    encode_result = encoder.encode_to_file(
        library, encoded_chars, str(output_path), options=options,
    )
    stats.time_encode = time.time() - t0
    stats.enc_size = encode_result.bytes_written

    print(f"  ✓ 写入: {output_path}")
    print(f"  ✓ 大小: {encode_result.bytes_written:,} bytes")

    # -------- 5. 解码验证 --------
    print("\n[5/5] 解码验证 ...")
    t0 = time.time()

    decoded = encoder.decode_from_file(str(output_path))
    stats.time_decode = time.time() - t0
    print(f"  ✓ 解码 {len(decoded.chars)} 个字符, {len(decoded.components)} 个部件")

    # 一致性检查
    assert len(decoded.chars) == len(encoded_chars)
    assert len(decoded.components) == len(library)

    stats.print()
    return stats


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HanFont Compress - 汉字字体压缩算法演示"
    )
    parser.add_argument(
        "--font", type=str, default="",
        help="TTF/OTF 字体文件路径 (留空则使用合成数据)"
    )
    parser.add_argument(
        "--output", type=str, default="output/demo_output.hfc",
        help=".hfc 输出文件路径"
    )
    parser.add_argument(
        "--max-chars", type=int, default=0,
        help="最大处理字符数 (0 = 全部)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()

    if args.font:
        font_path = Path(args.font).resolve()
        if not font_path.exists():
            print(f"错误: 字体文件不存在: {font_path}")
            return 1
        run_font_demo(font_path, output_path, max_chars=args.max_chars)
    else:
        run_synthetic_demo(output_path, max_chars=args.max_chars)

    print("\n完成 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
