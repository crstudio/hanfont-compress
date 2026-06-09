"""
GlyphReconstructor 单元测试。
"""

from __future__ import annotations

import pytest

from hfc.component_library import Component, ComponentLibrary
from hfc.component_matcher import EncodedChar, PartInstance, Transform
from hfc.glyph_extractor import Contour, GlyphContours
from hfc.reconstructor import GlyphReconstructor


def _make_square_glyph(uv: int, size: int = 1000) -> GlyphContours:
    g = GlyphContours(unicode=uv)
    c = g.add_contour()
    c.add_point(0, 0, True)
    c.add_point(size, 0, True)
    c.add_point(size, size, True)
    c.add_point(0, size, True)
    g.recompute_bbox()
    return g


def test_reconstruct_component_mode_basic():
    """部件编码模式: 重建后应产生非空轮廓。"""
    lib = ComponentLibrary()
    comp = Component(id="sq", name="方形", semantic="口")
    comp.add_sample_from_glyph(_make_square_glyph(0x0001, size=1000))
    lib.add_component(comp)

    enc = EncodedChar(unicode=0x4E00, mode="COMPONENT")
    t = Transform(a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0)
    enc.parts.append(
        PartInstance(component_id="sq", transform=t, similarity=1.0)
    )

    recon = GlyphReconstructor(lib)
    result = recon.reconstruct(enc)

    assert result.mode == "COMPONENT"
    assert result.component_ids == ["sq"]
    assert not result.glyph.is_empty()
    # 轮廓点数量应与部件一致
    assert result.glyph.get_point_count() == 4


def test_reconstruct_component_mode_with_scaling():
    """部件编码模式 + 仿射缩放: 坐标应被缩放。"""
    lib = ComponentLibrary()
    comp = Component(id="sq", name="方形", semantic="口")
    comp.add_sample_from_glyph(_make_square_glyph(0x0001, size=1000))
    lib.add_component(comp)

    enc = EncodedChar(unicode=0x4E00, mode="COMPONENT")
    # 缩放 0.5 + 平移 (100, 100)
    t = Transform(a=0.5, b=0.0, c=0.0, d=0.5, tx=100.0, ty=100.0)
    enc.parts.append(
        PartInstance(component_id="sq", transform=t, similarity=0.9)
    )

    recon = GlyphReconstructor(lib)
    result = recon.reconstruct(enc)
    result.glyph.recompute_bbox()
    x_min, y_min, x_max, y_max = result.glyph.bbox

    # 原始 [0,1000] → 缩放 0.5 + 平移 100 → [100, 600]
    assert x_min == pytest.approx(100, abs=2)
    assert y_min == pytest.approx(100, abs=2)
    assert x_max == pytest.approx(600, abs=2)
    assert y_max == pytest.approx(600, abs=2)


def test_reconstruct_raw_mode():
    """RAW 模式: 应原样返回存储的轮廓。"""
    lib = ComponentLibrary()
    enc = EncodedChar(unicode=0x4E00, mode="RAW")

    g = _make_square_glyph(0x4E00, size=800)
    enc.raw_contours = list(g.contours)

    recon = GlyphReconstructor(lib)
    result = recon.reconstruct(enc)

    assert result.mode == "RAW"
    assert result.component_ids == []
    assert result.glyph.get_point_count() == 4


def test_reconstruct_missing_component_falls_through():
    """部件在库中缺失 → 跳过并返回空(或部分)结果。"""
    lib = ComponentLibrary()
    enc = EncodedChar(unicode=0x4E00, mode="COMPONENT")
    t = Transform(a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0)
    enc.parts.append(
        PartInstance(component_id="MISSING", transform=t, similarity=0.5)
    )

    recon = GlyphReconstructor(lib)
    result = recon.reconstruct(enc)

    assert result.mode == "COMPONENT"
    assert "MISSING" not in result.component_ids  # 不应包含缺失 id
    assert result.glyph.is_empty()


def test_bbox_recompute_added_in_extractor():
    """确认 GlyphContours 具备 recompute_bbox 方法。"""
    g = GlyphContours(unicode=0)
    c = g.add_contour()
    c.add_point(-100, -200, True)
    c.add_point(300, 400, True)
    g.recompute_bbox()
    assert g.bbox[0] == -100
    assert g.bbox[1] == -200
    assert g.bbox[2] == 300
    assert g.bbox[3] == 400
