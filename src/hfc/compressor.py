"""
汉字字体压缩器——统一入口模块。

三个方案:
  - 路线A (route_a): 无 Delta 有损压缩。Bitmap IoU 部件匹配。
  - 路线B (route_b): 带 Delta 近无损压缩。在路线A基础上加差分修正。
  - 路线C (route_c): TrueType Composite Glyph 标准字体内复用。

使用示例:
    from hfc.compressor import (
        # 路线A
        RouteAConfig, RouteAEncoder, RouteAResult,
        Transform2D, MatchedComponent, CompressedGlyph,
        # 路线B
        RouteBConfig, RouteBEncoder, RouteBResult,
        DeltaData,
        # 路线C
        RouteCConfig, RouteCEncoder, RouteCResult,
        CompositeEncoder,  # 向后兼容别名
    )

参考: docs/try.md 第 6 节（三条实验路线）
"""

from __future__ import annotations

# ---- 路线A ----
from .route_a import (
    RouteAConfig,
    RouteAEncoder,
    RouteAResult,
    Transform2D,
    MatchedComponent,
    CompressedGlyph,
    bitmap_iou,
    fit_transform,
    match_candidate,
    rasterize_contours_to_bitmap,
)

# ---- 路线B ----
from .route_b import (
    RouteBConfig,
    RouteBEncoder,
    RouteBResult,
    DeltaData,
    DeltaResult,
    evaluate_delta_cost,
)

# ---- 路线C ----
from .composite_encoder import (
    RouteCConfig,
    RouteCEncoder,
    RouteCResult,
    ComponentInfo,
    CompositeEncoder,  # 向后兼容别名
)

__all__ = [
    # 路线A
    "RouteAConfig",
    "RouteAEncoder",
    "RouteAResult",
    "Transform2D",
    "MatchedComponent",
    "CompressedGlyph",
    "bitmap_iou",
    "fit_transform",
    "match_candidate",
    "rasterize_contours_to_bitmap",
    # 路线B
    "RouteBConfig",
    "RouteBEncoder",
    "RouteBResult",
    "DeltaData",
    "DeltaResult",
    "evaluate_delta_cost",
    # 路线C
    "RouteCConfig",
    "RouteCEncoder",
    "RouteCResult",
    "ComponentInfo",
    "CompositeEncoder",
]


# ============================================================================
# Demo：演示三个方案的完整流程
# ============================================================================

if __name__ == "__main__":
    import time
    from .glyph_extractor import GlyphContours, Contour, ContourPoint

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

    print("=" * 60)
    print("汉字字体压缩器 - 三个方案 Demo")
    print("=" * 60)

    # ---- 路线A ----
    print("\n[路线A] 无 Delta 有损压缩 ...")
    t0 = time.time()
    enc_a = RouteAEncoder(RouteAConfig(
        bitmap_size=64, iou_threshold=0.85,
        min_component_size=150, seed_chars=1,
    ))
    res_a = enc_a.encode(glyphs)
    print(f"  耗时: {(time.time()-t0)*1000:.1f}ms")
    print(res_a.summary())

    # ---- 路线B ----
    print("\n[路线B] 带 Delta 近无损压缩 ...")
    t0 = time.time()
    enc_b = RouteBEncoder(RouteBConfig(
        bitmap_size=64, iou_threshold=0.85,
        min_component_size=150, seed_chars=1,
        delta_point_ratio=0.8, max_delta_points=20,
    ))
    res_b = enc_b.encode(glyphs)
    print(f"  耗时: {(time.time()-t0)*1000:.1f}ms")
    print(res_b.summary())

    # ---- 路线C ----
    print("\n[路线C] TrueType Composite Glyph (需要真实 TTF 文件)")
    print("  提示: 使用 python -m hfc.route_c 运行路线C演示")

    print("\nDemo 完成 ✓")
