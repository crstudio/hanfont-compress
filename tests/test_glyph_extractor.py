"""
测试模块1: 字形轮廓提取器 (GlyphExtractor)

Usage:
    pytest tests/test_glyph_extractor.py -v
"""

import sys
from pathlib import Path

import pytest

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hfc.glyph_extractor import (
    CJK_UNIFIED_END,
    CJK_UNIFIED_START,
    Contour,
    ContourPoint,
    GlyphContours,
    GlyphExtractResult,
    GlyphExtractor,
    GlyphExtractorConfig,
)


class TestContourPoint:
    """测试 ContourPoint 数据类"""

    def test_creation(self):
        """测试创建点"""
        pt = ContourPoint(x=100, y=200, is_on_curve=True)
        assert pt.x == 100
        assert pt.y == 200
        assert pt.is_on_curve is True

    def test_to_float(self):
        """测试转换为浮点坐标 (F26Dot6 格式)"""
        # F26Dot6: 整数 64 表示 1.0
        pt = ContourPoint(x=6400, y=3200, is_on_curve=True)
        fx, fy = pt.to_float()
        assert abs(fx - 100.0) < 0.001
        assert abs(fy - 50.0) < 0.001

    def test_repr(self):
        """测试字符串表示"""
        pt_on = ContourPoint(x=100, y=200, is_on_curve=True)
        pt_ctrl = ContourPoint(x=100, y=200, is_on_curve=False)
        assert "O" in repr(pt_on)  # On-curve
        assert "C" in repr(pt_ctrl)  # Control point


class TestContour:
    """测试 Contour 轮廓类"""

    def test_add_point(self):
        """测试添加点"""
        contour = Contour()
        contour.add_point(0, 0, True)
        contour.add_point(100, 0, False)
        contour.add_point(100, 100, True)

        assert len(contour) == 3
        assert contour[0].x == 0
        assert contour[1].is_on_curve is False

    def test_is_empty(self):
        """测试空轮廓判断"""
        empty = Contour()
        assert empty.is_empty() is True

        empty.add_point(0, 0, True)
        assert empty.is_empty() is False

    def test_iter(self):
        """测试迭代"""
        contour = Contour()
        contour.add_point(0, 0, True)
        contour.add_point(100, 0, False)

        points = list(contour)
        assert len(points) == 2
        assert points[0].x == 0


class TestGlyphContours:
    """测试 GlyphContours 字形数据类"""

    def test_creation(self):
        """测试创建字形数据"""
        glyph = GlyphContours(unicode=0x6C49)
        assert glyph.unicode == 0x6C49
        assert len(glyph.contours) == 0
        assert glyph.bbox == (0, 0, 0, 0)

    def test_add_contour(self):
        """测试添加轮廓"""
        glyph = GlyphContours(unicode=0x6C49)
        c1 = glyph.add_contour()
        c2 = glyph.add_contour()

        assert len(glyph.contours) == 2
        assert c1 is not c2

    def test_get_point_count(self):
        """测试获取总点数"""
        glyph = GlyphContours(unicode=0x6C49)

        c1 = glyph.add_contour()
        c1.add_point(0, 0, True)
        c1.add_point(100, 0, False)

        c2 = glyph.add_contour()
        c2.add_point(0, 0, True)
        c2.add_point(100, 100, True)
        c2.add_point(200, 100, True)

        assert glyph.get_point_count() == 5

    def test_bounding_box_float(self):
        """测试浮点包围盒"""
        glyph = GlyphContours(unicode=0x6C49, bbox=(6400, 3200, 12800, 9600))
        x_min, y_min, x_max, y_max = glyph.bounding_box()

        assert abs(x_min - 100.0) < 0.001
        assert abs(y_min - 50.0) < 0.001
        assert abs(x_max - 200.0) < 0.001
        assert abs(y_max - 150.0) < 0.001


class TestGlyphExtractorConfig:
    """测试配置类"""

    def test_defaults(self):
        """测试默认配置"""
        config = GlyphExtractorConfig()
        assert config.cjk_only is True
        assert config.include_extension_b is False
        assert config.include_compat is False
        assert config.units_per_em == 1000

    def test_custom_config(self):
        """测试自定义配置"""
        config = GlyphExtractorConfig(
            cjk_only=False,
            include_extension_b=True,
            include_compat=True,
            units_per_em=2048,
        )
        assert config.cjk_only is False
        assert config.include_extension_b is True


class TestCJKRange:
    """测试 CJK Unicode 范围判断"""

    def test_basic_range(self):
        """测试基本区范围"""
        extractor = GlyphExtractor()

        # 常用汉字 "一" U+4E00
        assert extractor._is_cjk(0x4E00) is True
        # "龥" U+9FA5 最后一个汉字
        assert extractor._is_cjk(0x9FA5) is True
        # 非汉字
        assert extractor._is_cjk(0x0041) is False  # A
        assert extractor._is_cjk(0x3000) is False  # 空格

    def test_extension_b(self):
        """测试扩展区 B"""
        config = GlyphExtractorConfig(include_extension_b=True)
        extractor = GlyphExtractor(config)

        # 扩展区 B 常用字
        assert extractor._is_cjk(0x20000) is True

        # 默认不包含扩展区
        extractor_default = GlyphExtractor()
        assert extractor_default._is_cjk(0x20000) is False

    def test_compat_range(self):
        """测试兼容区"""
        config = GlyphExtractorConfig(include_compat=True)
        extractor = GlyphExtractor(config)

        # CJK 兼容区
        assert extractor._is_cjk(0xF900) is True


class TestGlyphExtractResult:
    """测试提取结果类"""

    def test_success_result(self):
        """测试成功结果"""
        glyph = GlyphContours(unicode=0x6C49)
        result = GlyphExtractResult(glyph=glyph, success=True)

        assert result.success is True
        assert result.error_message == ""
        assert result.glyph.unicode == 0x6C49

    def test_failure_result(self):
        """测试失败结果"""
        result = GlyphExtractResult(
            glyph=GlyphContours(unicode=0x6C49),
            success=False,
            error_message="No glyph found",
        )

        assert result.success is False
        assert result.error_message == "No glyph found"


class TestGlyphExtractorIntegration:
    """
    集成测试 (需要实际字体文件)

    这些测试需要字体文件才能运行。
    可以从以下来源获取测试字体:
    - 思源黑体: https://github.com/adobe-fonts/source-han-sans/releases
    - 思源宋体: https://github.com/adobe-fonts/source-han-serif/releases
    - 文泉驿字体: https://github.com/ubutnu/fonts
    """

    @pytest.fixture
    def font_path(self, tmp_path):
        """
        返回测试字体路径。

        默认跳过，需要设置 FONTTEST_PATH 环境变量指向 TTF 文件。
        """
        font_path = tmp_path / "test_font.ttf"

        # 尝试从环境变量获取测试字体
        import os

        test_font = os.environ.get("FONTTEST_PATH")
        if test_font and Path(test_font).exists():
            return Path(test_font)

        pytest.skip("No test font file available. Set FONTTEST_PATH environment variable.")

    def test_extract_single_char(self, font_path):
        """测试提取单个汉字"""
        extractor = GlyphExtractor()

        # 提取 "中" U+4E2D
        result = extractor.extract_from_file(font_path, 0x4E2D)

        assert result.success is True, result.error_message
        assert result.glyph.unicode == 0x4E2D
        assert result.glyph.bbox != (0, 0, 0, 0)

    def test_extract_all_cjk(self, font_path):
        """测试提取所有 CJK 字符"""
        extractor = GlyphExtractor()
        results = list(extractor.extract_all_cjk(font_path))

        # 至少应该有一些汉字
        success_count = sum(1 for r in results if r.success)
        assert success_count > 0

    def test_batch_extract(self, font_path):
        """测试批量提取"""
        extractor = GlyphExtractor()
        chars = [0x4E2D, 0x6587, 0x5B57]  # 中、文字

        results = extractor.extract_batch(font_path, chars)

        assert len(results) == 3
        assert all(r.success for r in results)

    def test_nonexistent_char(self, font_path):
        """测试提取不存在的字符"""
        extractor = GlyphExtractor()

        # 拉丁字母通常不在中文字体中
        result = extractor.extract_from_file(font_path, 0x0041)

        # 应该失败或返回空
        if not result.success:
            assert "not found" in result.error_message.lower() or "no glyph" in result.error_message.lower()
