"""
模块5 (部分): 字形渲染器

将 GlyphContours 渲染为位图图像，用于：
1. 像素级验证（对比原始字体渲染 vs 还原结果）
2. Web UI 人工审核工具的图像预览

参考: docs/technical-design.md 第 3.1 节 (GlyphContours)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw

from .glyph_extractor import Contour, ContourPoint, GlyphContours


# 默认渲染尺寸
DEFAULT_EM_SIZE = 1000  # 与字体 units_per_em 对应
DEFAULT_POINT_SIZE = 64  # 渲染字号
DEFAULT_DPI = 96


@dataclass
class RenderOptions:
    """渲染选项"""

    # 输出图像尺寸 (宽, 高)
    size: tuple[int, int] = (128, 128)

    # 背景颜色 (R, G, B, A)
    background: tuple[int, int, int, int] = (255, 255, 255, 255)

    # 前景色 ( glyph 填充色)
    foreground: tuple[int, int, int, int] = (0, 0, 0, 255)

    # 是否反锯齿
    antialias: bool = True

    # 水平边距
    padding_h: int = 10

    # 垂直边距
    padding_v: int = 10


@dataclass
class RenderResult:
    """渲染结果"""

    image: Image.Image
    success: bool
    error_message: str = ""


class GlyphRenderer:
    """
    字形轮廓渲染器。

    将 GlyphContours 数据渲染为 PIL Image 对象。

    Usage:
        renderer = GlyphRenderer()
        options = RenderOptions(size=(128, 128))

        # 渲染单个字形
        result = renderer.render(glyph, options)
        if result.success:
            result.image.save("output.png")

        # 渲染并排对比
        comparison = renderer.render_comparison(original_glyph, reconstructed_glyph)
        comparison.save("comparison.png")
    """

    def __init__(self):
        pass

    def render(
        self, glyph: GlyphContours, options: RenderOptions | None = None
    ) -> RenderResult:
        """
        渲染单个字形为图像。

        Args:
            glyph: 字形轮廓数据
            options: 渲染选项

        Returns:
            RenderResult: 包含 PIL Image 对象
        """
        if options is None:
            options = RenderOptions()

        if glyph.is_empty():
            return RenderResult(
                image=Image.new("RGBA", options.size, options.background),
                success=False,
                error_message="Empty glyph contours",
            )

        try:
            # 计算字形的缩放比例和偏移，使其适应图像尺寸
            scale, offset_x, offset_y = self._compute_transform(
                glyph, options.size, options.padding_h, options.padding_v
            )

            # 创建图像
            img = Image.new("RGBA", options.size, options.background)
            draw = ImageDraw.Draw(img, "RGBA")

            # 绘制每个轮廓
            for contour in glyph.contours:
                if contour.is_empty():
                    continue

                # 转换点坐标
                points = self._transform_contour(contour, scale, offset_x, offset_y)

                # 绘制填充多边形
                if len(points) >= 3:
                    draw.polygon(points, fill=options.foreground)

            return RenderResult(image=img, success=True)

        except Exception as e:
            return RenderResult(
                image=Image.new("RGBA", options.size, options.background),
                success=False,
                error_message=f"Render failed: {e}",
            )

    def render_to_array(
        self, glyph: GlyphContours, options: RenderOptions | None = None
    ) -> RenderResult:
        """
        渲染为 NumPy 数组。

        Args:
            glyph: 字形轮廓数据
            options: 渲染选项

        Returns:
            RenderResult: image 字段为 NumPy uint8 数组 (H, W, 4)
        """
        result = self.render(glyph, options)
        if result.success:
            result.image = np.array(result.image)
        return result

    def render_comparison(
        self,
        original: GlyphContours,
        reconstructed: GlyphContours,
        options: RenderOptions | None = None,
    ) -> RenderResult:
        """
        渲染并排对比图。

        左边: 原始字形
        右边: 还原字形

        Args:
            original: 原始字形轮廓
            reconstructed: 还原后的字形轮廓
            options: 渲染选项（会影响单个子图的尺寸）

        Returns:
            RenderResult: 包含并排对比的 PIL Image
        """
        if options is None:
            options = RenderOptions()

        # 计算并排图像尺寸
        w, h = options.size
        comparison_size = (w * 2 + 20, h)  # 20px 分隔线

        result_left = self.render(original, options)
        result_right = self.render(reconstructed, options)

        # 创建并排图像
        comparison = Image.new("RGBA", comparison_size, options.background)
        draw = ImageDraw.Draw(comparison)

        # 合并两张图
        if result_left.success:
            comparison.paste(result_left.image, (0, 0))
        if result_right.success:
            comparison.paste(result_right.image, (w + 20, 0))

        # 画分隔线
        line_color = (128, 128, 128, 255)
        draw.line([(w + 10, 0), (w + 10, h)], fill=line_color, width=1)

        return RenderResult(image=comparison, success=True)

    def render_grid(
        self,
        glyphs: Sequence[tuple[str, GlyphContours]],
        cols: int = 4,
        options: RenderOptions | None = None,
    ) -> RenderResult:
        """
        渲染网格图。

        用于预览多个字形。

        Args:
            glyphs: (label, glyph) 元组列表
            cols: 列数
            options: 渲染选项（会影响每个子图的尺寸）

        Returns:
            RenderResult: 网格图像
        """
        if options is None:
            options = RenderOptions()

        if not glyphs:
            return RenderResult(
                image=Image.new("RGBA", (1, 1), options.background),
                success=True,
            )

        rows = (len(glyphs) + cols - 1) // cols
        w, h = options.size
        gap = 10
        grid_w = cols * w + (cols - 1) * gap
        grid_h = rows * h + (rows - 1) * gap + 30  # 30px 标题区域

        grid = Image.new("RGBA", (grid_w, grid_h), options.background)
        draw = ImageDraw.Draw(grid)

        for idx, (label, glyph) in enumerate(glyphs):
            col = idx % cols
            row = idx // cols
            x = col * (w + gap)
            y = row * (h + gap) + 30  # 30px 标题偏移

            # 渲染字形
            result = self.render(glyph, options)
            if result.success:
                grid.paste(result.image, (x, y))
                # 绘制标签
                draw.text((x, y - 25), label, fill=(0, 0, 0, 255))

        return RenderResult(image=grid, success=True)

    def _compute_transform(
        self,
        glyph: GlyphContours,
        output_size: tuple[int, int],
        padding_h: int,
        padding_v: int,
    ) -> tuple[float, float, float]:
        """
        计算从字形坐标到图像坐标的变换参数。

        Returns:
            (scale, offset_x, offset_y)
            变换公式: image_x = glyph_x * scale + offset_x
        """
        x_min, y_min, x_max, y_max = glyph.bbox

        if x_max == x_min or y_max == y_min:
            # 处理空边界框
            scale = 1.0
            offset_x = output_size[0] / 2
            offset_y = output_size[1] / 2
            return scale, offset_x, offset_y

        # 转换 F26Dot6 到浮点
        x_min_f = x_min / 64.0
        y_min_f = y_min / 64.0
        x_max_f = x_max / 64.0
        y_max_f = y_max / 64.0

        glyph_width = x_max_f - x_min_f
        glyph_height = y_max_f - y_min_f

        # 可用输出尺寸（减去 padding）
        avail_w = output_size[0] - 2 * padding_h
        avail_h = output_size[1] - 2 * padding_v

        # 计算缩放比例，保持宽高比
        scale_x = avail_w / glyph_width if glyph_width > 0 else 1.0
        scale_y = avail_h / glyph_height if glyph_height > 0 else 1.0
        scale = min(scale_x, scale_y)

        # 计算偏移量使字形居中
        scaled_width = glyph_width * scale
        scaled_height = glyph_height * scale
        offset_x = padding_h + (avail_w - scaled_width) / 2 - x_min_f * scale
        offset_y = padding_h + (avail_h - scaled_height) / 2 - y_min_f * scale

        return scale, offset_x, offset_y

    def _transform_contour(
        self, contour: Contour, scale: float, offset_x: float, offset_y: float
    ) -> list[tuple[int, int]]:
        """
        将轮廓点变换为图像坐标。

        注意: 只处理 on-curve 点，因为填充只需要多边形顶点。
        对于 off-curve 控制点，需要先用 de Casteljau 算法插值。

        Returns:
            [(x, y), ...] 整数坐标列表
        """
        points: list[tuple[int, int]] = []

        # 获取所有 on-curve 点
        on_curve = [pt for pt in contour if pt.is_on_curve]

        if len(on_curve) < 2:
            # 只有一个点或没有点
            if on_curve:
                x = int(on_curve[0].x / 64.0 * scale + offset_x)
                y = int(on_curve[0].y / 64.0 * scale + offset_y)
                return [(x, y)]
            return []

        # 构建多边形顶点列表
        # 注意: 这是简化版本，真实实现需要处理二次贝塞尔的曲线插值
        for pt in on_curve:
            x = int(pt.x / 64.0 * scale + offset_x)
            y = int(pt.y / 64.0 * scale + offset_y)
            points.append((x, y))

        return points


def render_glyph_to_image(
    glyph: GlyphContours,
    size: tuple[int, int] = (128, 128),
    bg: tuple[int, int, int, int] = (255, 255, 255, 255),
    fg: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> Image.Image:
    """
    便捷函数: 快速渲染单个字形。

    Usage:
        img = render_glyph_to_image(glyph, size=(64, 64))
        img.save("glyph.png")
    """
    options = RenderOptions(size=size, background=bg, foreground=fg)
    renderer = GlyphRenderer()
    result = renderer.render(glyph, options)
    return result.image
