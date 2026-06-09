"""
测试模块5 (部分): 字形渲染器 (GlyphRenderer)

Usage:
    pytest tests/test_renderer.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours
from hfc.renderer import (
    DEFAULT_DPI,
    DEFAULT_EM_SIZE,
    DEFAULT_POINT_SIZE,
    GlyphRenderer,
    RenderOptions,
    RenderResult,
    render_glyph_to_image,
)


class TestRenderOptions:
    """测试 RenderOptions 配置类"""

    def test_defaults(self):
        """测试默认配置"""
        opts = RenderOptions()
        assert opts.size == (128, 128)
        assert opts.background == (255, 255, 255, 255)
        assert opts.foreground == (0, 0, 0, 255)
        assert opts.antialias is True
        assert opts.padding_h == 10
        assert opts.padding_v == 10

    def test_custom(self):
        """测试自定义配置"""
        opts = RenderOptions(
            size=(256, 256),
            background=(0, 0, 0, 255),
            foreground=(255, 255, 255, 255),
            padding_h=20,
            padding_v=20,
        )
        assert opts.size == (256, 256)
        assert opts.background == (0, 0, 0, 255)
        assert opts.foreground == (255, 255, 255, 255)
        assert opts.padding_h == 20


class TestRenderResult:
    """测试 RenderResult 结果类"""

    def test_success(self):
        """测试成功结果"""
        img = Image.new("RGBA", (100, 100))
        result = RenderResult(image=img, success=True)
        assert result.success is True
        assert result.error_message == ""

    def test_failure(self):
        """测试失败结果"""
        result = RenderResult(
            image=Image.new("RGBA", (100, 100)),
            success=False,
            error_message="Test error",
        )
        assert result.success is False
        assert result.error_message == "Test error"


class TestGlyphRendererCreation:
    """测试 GlyphRenderer 创建"""

    def test_creation(self):
        """测试创建渲染器"""
        renderer = GlyphRenderer()
        assert renderer is not None


class TestGlyphRendererBasic:
    """测试 GlyphRenderer 基本功能（无需真实字体文件）"""

    def _make_simple_glyph(self) -> GlyphContours:
        """创建一个简单的测试用字形（一个方形轮廓）"""
        # 方形轮廓: (0,0) -> (100,0) -> (100,100) -> (0,100) -> (0,0)
        # F26Dot6 格式: 64 = 1.0
        glyph = GlyphContours(
            unicode=0x6C49,
            bbox=(0, 0, 6400, 6400),  # F26Dot6
        )
        contour = glyph.add_contour()
        # 四个角点
        contour.add_point(0, 0, True)       # 左下
        contour.add_point(6400, 0, True)     # 右下
        contour.add_point(6400, 6400, True)  # 右上
        contour.add_point(0, 6400, True)     # 左上
        return glyph

    def _make_complex_glyph(self) -> GlyphContours:
        """创建一个复杂字形（两个轮廓 - 外框和内洞）"""
        glyph = GlyphContours(
            unicode=0x56DB,
            bbox=(-100, -100, 7100, 7100),  # F26Dot6
        )
        # 外轮廓
        outer = glyph.add_contour()
        outer.add_point(-100, -100, True)
        outer.add_point(7100, -100, True)
        outer.add_point(7100, 7100, True)
        outer.add_point(-100, 7100, True)
        # 内轮廓 (洞)
        inner = glyph.add_contour()
        inner.add_point(100, 100, True)
        inner.add_point(100, 6400, True)
        inner.add_point(6400, 6400, True)
        inner.add_point(6400, 100, True)
        return glyph

    def _make_empty_glyph(self) -> GlyphContours:
        """创建空字形"""
        return GlyphContours(unicode=0x0000, bbox=(0, 0, 0, 0))

    def test_render_simple_glyph(self):
        """测试渲染简单字形"""
        renderer = GlyphRenderer()
        glyph = self._make_simple_glyph()
        opts = RenderOptions(size=(128, 128))

        result = renderer.render(glyph, opts)

        assert result.success is True
        assert isinstance(result.image, Image.Image)
        assert result.image.size == (128, 128)
        assert result.image.mode == "RGBA"

    def test_render_complex_glyph(self):
        """测试渲染复杂字形（多个轮廓）"""
        renderer = GlyphRenderer()
        glyph = self._make_complex_glyph()
        opts = RenderOptions(size=(128, 128))

        result = renderer.render(glyph, opts)

        assert result.success is True
        assert result.image.size == (128, 128)

    def test_render_empty_glyph(self):
        """测试渲染空字形"""
        renderer = GlyphRenderer()
        glyph = self._make_empty_glyph()
        opts = RenderOptions()

        result = renderer.render(glyph, opts)

        # 空字形应该返回空图像但不算失败
        assert isinstance(result.image, Image.Image)
        # 可以检查图像是纯白色的（背景色）
        arr = np.array(result.image)
        # 检查是否全是白色像素（RGBA = 255,255,255,255）
        assert np.all(arr[:, :, 0] == 255)

    def test_render_to_array(self):
        """测试渲染为 NumPy 数组"""
        renderer = GlyphRenderer()
        glyph = self._make_simple_glyph()
        opts = RenderOptions(size=(64, 64))

        result = renderer.render_to_array(glyph, opts)

        assert result.success is True
        assert isinstance(result.image, np.ndarray)
        assert result.image.shape == (64, 64, 4)
        assert result.image.dtype == np.uint8


class TestRenderComparison:
    """测试并排对比渲染"""

    def _make_glyph_a(self) -> GlyphContours:
        glyph = GlyphContours(unicode=0x4E00, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)
        c.add_point(0, 6400, True)
        return glyph

    def _make_glyph_b(self) -> GlyphContours:
        glyph = GlyphContours(unicode=0x4E01, bbox=(100, 100, 6600, 6600))
        c = glyph.add_contour()
        c.add_point(100, 100, True)
        c.add_point(6600, 100, True)
        c.add_point(6600, 6600, True)
        c.add_point(100, 6600, True)
        return glyph

    def test_render_comparison(self):
        """测试并排对比"""
        renderer = GlyphRenderer()
        glyph_a = self._make_glyph_a()
        glyph_b = self._make_glyph_b()
        opts = RenderOptions(size=(100, 100))

        result = renderer.render_comparison(glyph_a, glyph_b, opts)

        assert result.success is True
        # 并排: 100 + 20 + 100 = 220
        assert result.image.size == (220, 100)

    def test_render_comparison_with_none(self):
        """测试对比中包含无效字形"""
        renderer = GlyphRenderer()
        glyph = self._make_glyph_a()
        empty = GlyphContours(unicode=0x0000)
        opts = RenderOptions(size=(80, 80))

        result = renderer.render_comparison(glyph, empty, opts)

        # 应该仍然返回图像，只是右边是空白
        assert isinstance(result.image, Image.Image)
        assert result.image.size == (180, 80)


class TestRenderGrid:
    """测试网格渲染"""

    def test_render_grid_empty(self):
        """测试空网格"""
        renderer = GlyphRenderer()
        opts = RenderOptions(size=(64, 64))

        result = renderer.render_grid([], cols=4, options=opts)

        assert result.success is True
        # 空网格应该返回一个很小的图像
        assert result.image.size[1] > 0

    def test_render_grid_single(self):
        """测试单个字形的网格"""
        renderer = GlyphRenderer()
        glyph = GlyphContours(unicode=0x4E00, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)
        c.add_point(0, 6400, True)

        opts = RenderOptions(size=(64, 64))
        result = renderer.render_grid([("测试", glyph)], cols=2, options=opts)

        assert result.success is True
        # 64*2 + 10 = 138
        assert result.image.size[0] == 138

    def test_render_grid_multiple(self):
        """测试多个字形的网格"""
        renderer = GlyphRenderer()
        glyphs = []

        for i in range(5):
            glyph = GlyphContours(unicode=0x4E00 + i, bbox=(0, 0, 6400, 6400))
            c = glyph.add_contour()
            c.add_point(0, 0, True)
            c.add_point(6400, 0, True)
            c.add_point(6400, 6400, True)
            c.add_point(0, 6400, True)
            glyphs.append((f"字{i}", glyph))

        opts = RenderOptions(size=(64, 64))
        # 5 个字形, 2 列 = 3 行
        result = renderer.render_grid(glyphs, cols=2, options=opts)

        assert result.success is True
        # 3 行: 3*64 + 2*10 + 30 = 242
        # 2 列: 2*64 + 1*10 = 138
        assert result.image.size[0] == 138
        assert result.image.size[1] == 242


class TestConvenienceFunction:
    """测试便捷函数"""

    def test_render_glyph_to_image(self):
        """测试便捷函数"""
        glyph = GlyphContours(unicode=0x6C49, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)
        c.add_point(0, 6400, True)

        img = render_glyph_to_image(glyph, size=(64, 64))

        assert isinstance(img, Image.Image)
        assert img.size == (64, 64)
        assert img.mode == "RGBA"


class TestComputeTransform:
    """测试坐标变换计算"""

    def test_centered_output(self):
        """测试字形居中"""
        renderer = GlyphRenderer()

        # 100x100 的字形
        glyph = GlyphContours(unicode=0x0000, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)
        c.add_point(0, 6400, True)

        scale, ox, oy = renderer._compute_transform(
            glyph, output_size=(128, 128), padding_h=10, padding_v=10
        )

        # 字形填充可用空间，偏移 = padding = 10
        # offset_x = padding_h + (avail_w - scaled_w)/2 - x_min*scale
        #          = 10 + (108 - 108)/2 - 0 = 10
        assert 8 < ox < 12
        assert 8 < oy < 12

    def test_non_square_bbox(self):
        """测试非正方形边界框"""
        renderer = GlyphRenderer()

        # 宽高比 2:1 的字形
        glyph = GlyphContours(unicode=0x0000, bbox=(0, 0, 12800, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(12800, 0, True)
        c.add_point(12800, 6400, True)
        c.add_point(0, 6400, True)

        scale, ox, oy = renderer._compute_transform(
            glyph, output_size=(128, 64), padding_h=10, padding_v=10
        )

        # 宽度方向会被限制
        assert scale > 0

    def test_zero_bbox(self):
        """测试空边界框"""
        renderer = GlyphRenderer()
        glyph = GlyphContours(unicode=0x0000, bbox=(0, 0, 0, 0))

        scale, ox, oy = renderer._compute_transform(
            glyph, output_size=(128, 128), padding_h=10, padding_v=10
        )

        # 应该返回默认值
        assert scale == 1.0
        assert ox == 64
        assert oy == 64
