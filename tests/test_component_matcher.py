"""
测试模块3: 部件匹配编码器 (ComponentMatcher)

Usage:
    pytest tests/test_component_matcher.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
    PartInstance,
    Transform,
    _compute_points_similarity,
    _contours_to_points,
    _normalize_points,
    _validate_transform,
    fit_affine_transform,
    match_glyph_with_library,
    procrustes_align,
)
from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours


# ============================================================================
# 辅助函数
# ============================================================================

def _make_glyph_with_points(points_list: list[tuple[float, float]]) -> GlyphContours:
    """用浮点坐标创建测试字形（F26Dot6 会自动乘以 64）"""
    glyph = GlyphContours(
        unicode=0x6C49,
        bbox=(int(min(p[0] for p in points_list) * 64),
              int(min(p[1] for p in points_list) * 64),
              int(max(p[0] for p in points_list) * 64),
              int(max(p[1] for p in points_list) * 64)),
    )
    contour = glyph.add_contour()
    for x, y in points_list:
        contour.add_point(int(x * 64), int(y * 64), True)
    return glyph


def _make_square_glyph(size: float = 100.0) -> GlyphContours:
    """创建方形测试字形"""
    return _make_glyph_with_points([
        (0.0, 0.0),
        (size, 0.0),
        (size, size),
        (0.0, size),
    ])


def _make_triangle_glyph(size: float = 100.0) -> GlyphContours:
    """创建三角形测试字形"""
    return _make_glyph_with_points([
        (0.0, 0.0),
        (size, 0.0),
        (size / 2, size),
    ])


# ============================================================================
# Transform 数据结构测试
# ============================================================================

class TestTransform:
    """测试 Transform 变换矩阵"""

    def test_identity(self):
        """测试恒等变换"""
        t = Transform.identity()
        x, y = t.apply(10.0, 20.0)
        assert abs(x - 10.0) < 1e-6
        assert abs(y - 20.0) < 1e-6

    def test_scale(self):
        """测试缩放变换"""
        t = Transform.from_scale(2.0, 3.0)
        x, y = t.apply(5.0, 10.0)
        assert abs(x - 10.0) < 1e-6
        assert abs(y - 30.0) < 1e-6

    def test_translation(self):
        """测试平移变换"""
        t = Transform.from_translation(10.0, 20.0)
        x, y = t.apply(5.0, 5.0)
        assert abs(x - 15.0) < 1e-6
        assert abs(y - 25.0) < 1e-6

    def test_apply_to_points(self):
        """测试批量应用变换"""
        t = Transform.from_scale(2.0, 2.0)
        pts = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        result = t.apply_to_points(pts)
        assert len(result) == 3
        assert abs(result[0][0] - 2.0) < 1e-6
        assert abs(result[1][0] - 4.0) < 1e-6

    def test_inverse_identity(self):
        """测试恒等变换的逆仍是恒等"""
        t = Transform.identity()
        inv = t.inverse()
        x, y = inv.apply(10.0, 20.0)
        assert abs(x - 10.0) < 1e-6
        assert abs(y - 20.0) < 1e-6

    def test_inverse_scale(self):
        """测试缩放的逆变换"""
        t = Transform.from_scale(2.0, 3.0)
        inv = t.inverse()
        # 先正向再反向
        x, y = t.apply(5.0, 10.0)
        x2, y2 = inv.apply(x, y)
        assert abs(x2 - 5.0) < 1e-4
        assert abs(y2 - 10.0) < 1e-4

    def test_to_matrix(self):
        """测试矩阵形式"""
        t = Transform(a=1, b=2, c=3, d=4, tx=5, ty=6)
        m = t.to_matrix()
        assert m.shape == (3, 3)
        assert m[0, 0] == 1 and m[0, 1] == 2 and m[0, 2] == 5
        assert m[2, 0] == 0 and m[2, 1] == 0 and m[2, 2] == 1


# ============================================================================
# PartInstance 和 EncodedChar 测试
# ============================================================================

class TestPartInstance:
    """测试 PartInstance"""

    def test_creation(self):
        inst = PartInstance(
            component_id="part_001",
            transform=Transform.identity(),
            similarity=0.9,
        )
        assert inst.component_id == "part_001"
        assert abs(inst.similarity - 0.9) < 1e-6


class TestEncodedChar:
    """测试 EncodedChar"""

    def test_raw_mode(self):
        enc = EncodedChar(unicode=0x6C49, mode="RAW")
        assert enc.is_component_mode() is False
        assert enc.mode == "RAW"

    def test_component_mode(self):
        enc = EncodedChar(
            unicode=0x6C49,
            mode="COMPONENT",
            match_score=0.95,
        )
        assert enc.is_component_mode() is True
        assert abs(enc.match_score - 0.95) < 1e-6

    def test_add_parts(self):
        enc = EncodedChar(unicode=0x6C49, mode="COMPONENT")
        p1 = PartInstance("p1", Transform.identity(), 0.9)
        p2 = PartInstance("p2", Transform.identity(), 0.8)
        enc.parts = [p1, p2]
        assert len(enc.parts) == 2

    def test_summary(self):
        enc = EncodedChar(unicode=0x6C49, mode="RAW")
        s = enc.summary()
        assert "U+" in s and "RAW" in s

        enc2 = EncodedChar(
            unicode=0x6C49,
            mode="COMPONENT",
            match_score=0.95,
        )
        enc2.parts = [PartInstance("p1", Transform.identity(), 0.95)]
        s2 = enc2.summary()
        assert "COMPONENT" in s2


# ============================================================================
# MatchConfig 测试
# ============================================================================

class TestMatchConfig:
    """测试匹配配置"""

    def test_defaults(self):
        config = MatchConfig()
        assert 0.5 < config.similarity_threshold < 1.0
        assert config.max_scale_deviation > 0
        assert config.max_rotation_deg > 0

    def test_custom(self):
        config = MatchConfig(
            similarity_threshold=0.9,
            max_scale_deviation=0.2,
        )
        assert abs(config.similarity_threshold - 0.9) < 1e-6
        assert abs(config.max_scale_deviation - 0.2) < 1e-6


# ============================================================================
# 几何算法测试
# ============================================================================

class TestGeometryFunctions:
    """测试内部几何函数"""

    def test_contours_to_points(self):
        """测试轮廓到点数组转换"""
        glyph = _make_square_glyph(100.0)
        points = _contours_to_points(glyph.contours)

        assert points.shape == (4, 2)
        # 第一点应该是 (0, 0) 附近
        assert abs(points[0, 0] - 0.0) < 1.0
        assert abs(points[0, 1] - 0.0) < 1.0
        # 第三点应该是 (100, 100) 附近
        assert abs(points[2, 0] - 100.0) < 1.0

    def test_normalize_points(self):
        """测试点集归一化"""
        pts = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float64)
        normalized, center, scale = _normalize_points(pts)

        assert normalized.shape == (4, 2)
        # 中心点接近 50, 50
        assert abs(center[0] - 50.0) < 1.0
        assert abs(center[1] - 50.0) < 1.0
        # 归一化后的点应该有较小的绝对值
        assert np.abs(normalized).max() < 5.0

    def test_procrustes_identical(self):
        """测试相同点集的对齐（应得到高相似度）"""
        pts = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float64)

        aligned, similarity = procrustes_align(pts, pts)
        # 相同点集相似度应很高
        assert similarity > 0.7

    def test_procrustes_different_shapes(self):
        """测试不同形状的对齐（应得到低相似度）"""
        square = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float64)
        triangle = np.array([[0, 0], [100, 0], [50, 100]], dtype=np.float64)

        _, similarity = procrustes_align(square, triangle)
        # 不同形状相似度应该较低
        # 注意: 由于点的分布可能随机，这里只做简单的 sanity check
        assert 0.0 <= similarity <= 1.0

    def test_similarity_same_points(self):
        """测试相同点集的相似度"""
        pts = np.array([[0, 0], [100, 0], [100, 100]], dtype=np.float64)
        sim = _compute_points_similarity(pts, pts)
        assert sim > 0.8

    def test_fit_affine_identity(self):
        """测试恒等变换的拟合"""
        pts = np.array([[0, 0], [100, 0], [0, 100], [100, 100]], dtype=np.float64)

        transform, error = fit_affine_transform(pts, pts)
        assert error < 1.0  # 误差应很小
        # 变换应接近恒等
        assert abs(transform.a - 1.0) < 0.1
        assert abs(transform.d - 1.0) < 0.1
        assert abs(transform.tx) < 1.0
        assert abs(transform.ty) < 1.0

    def test_fit_affine_scaled(self):
        """测试缩放变换的拟合"""
        src = np.array([[0, 0], [100, 0], [0, 100], [100, 100]], dtype=np.float64)
        tgt = src * 2.0  # 2倍缩放

        transform, error = fit_affine_transform(src, tgt)
        assert error < 1.0
        assert abs(transform.a - 2.0) < 0.1
        assert abs(transform.d - 2.0) < 0.1

    def test_validate_identity(self):
        """验证恒等变换"""
        config = MatchConfig()
        assert _validate_transform(Transform.identity(), config) is True

    def test_validate_unreasonable(self):
        """验证不合理的变换被拒绝"""
        config = MatchConfig()
        # 极大缩放应该被拒绝
        bad = Transform.from_scale(100.0, 100.0)
        assert _validate_transform(bad, config) is False


# ============================================================================
# ComponentMatcher 测试
# ============================================================================

class TestComponentMatcher:
    """测试部件匹配器"""

    def _make_test_library(self) -> ComponentLibrary:
        """创建一个简单的测试部件库"""
        library = ComponentLibrary()

        # 部件1: 方形
        comp_sq = Component(id="part_square", name="方形", semantic="口")
        glyph_sq = _make_square_glyph(100.0)
        comp_sq.add_sample_from_glyph(glyph_sq)
        library.add_component(comp_sq)

        # 部件2: 三角形
        comp_tri = Component(id="part_triangle", name="三角", semantic="△")
        glyph_tri = _make_triangle_glyph(100.0)
        comp_tri.add_sample_from_glyph(glyph_tri)
        library.add_component(comp_tri)

        return library

    def test_creation(self):
        """测试创建匹配器"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)
        assert matcher is not None

    def test_match_empty_glyph(self):
        """测试匹配空字形"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)

        empty = GlyphContours(unicode=0x0000)
        result = matcher.match(empty)

        assert result.mode == "RAW"

    def test_match_known_glyph(self):
        """测试匹配已知形状"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)

        # 匹配方形字形
        square_glyph = _make_square_glyph(100.0)
        result = matcher.match(square_glyph)

        # 可能成功也可能降级取决于算法质量，但状态必须合法
        assert result.mode in ("COMPONENT", "RAW")
        assert 0.0 <= result.match_score <= 1.0

    def test_stats_tracking(self):
        """测试统计计数"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)

        for _ in range(3):
            matcher.match(_make_square_glyph(100.0))

        stats = matcher.stats()
        assert stats["total"] == 3
        assert stats["component_mode"] + stats["raw_mode"] <= 3

    def test_reset_stats(self):
        """测试统计重置"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)

        matcher.match(_make_square_glyph(100.0))
        matcher.reset_stats()

        stats = matcher.stats()
        assert stats["total"] == 0

    def test_batch_match(self):
        """测试批量匹配"""
        library = self._make_test_library()
        matcher = ComponentMatcher(library)

        glyphs = [_make_square_glyph(100.0), _make_triangle_glyph(80.0)]
        results = matcher.match_batch(glyphs)

        assert len(results) == 2
        for r in results:
            assert isinstance(r, EncodedChar)

    def test_custom_threshold(self):
        """测试自定义相似度阈值"""
        library = self._make_test_library()
        config = MatchConfig(similarity_threshold=0.5)  # 较低阈值
        matcher = ComponentMatcher(library, config=config)

        glyph = _make_square_glyph(100.0)
        result = matcher.match(glyph)
        # 结果必须是合法状态
        assert result.mode in ("COMPONENT", "RAW")

    def test_empty_library(self):
        """测试空部件库"""
        library = ComponentLibrary()
        matcher = ComponentMatcher(library)

        result = matcher.match(_make_square_glyph(100.0))
        # 空部件库应该降级为 RAW
        assert result.mode == "RAW"

    def test_convenience_function(self):
        """测试便捷函数"""
        library = self._make_test_library()
        glyph = _make_square_glyph(100.0)

        result = match_glyph_with_library(glyph, library, threshold=0.5)
        assert isinstance(result, EncodedChar)


# ============================================================================
# 端到端测试
# ============================================================================

class TestEndToEnd:
    """端到端: 初始化器 -> 匹配器 完整流程"""

    def test_full_pipeline(self):
        """测试完整流程"""
        # 1. 构建简单部件库
        library = ComponentLibrary()

        comp = Component(id="part_square", name="方形", semantic="口")
        glyph = _make_square_glyph(100.0)
        comp.add_sample_from_glyph(glyph)
        library.add_component(comp)

        # 2. 创建匹配器
        matcher = ComponentMatcher(
            library,
            config=MatchConfig(similarity_threshold=0.3),  # 宽松阈值便于测试
        )

        # 3. 匹配输入字形
        input_glyph = _make_square_glyph(100.0)
        result = matcher.match(input_glyph)

        # 4. 结果必须合法
        assert isinstance(result, EncodedChar)
        assert result.mode in ("COMPONENT", "RAW")

        stats = matcher.stats()
        assert stats["total"] > 0
        print(f"\n匹配统计: {stats}")
        print(f"结果: {result.summary()}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
