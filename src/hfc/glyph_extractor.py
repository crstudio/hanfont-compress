"""
模块1: 字形轮廓提取器

从 TTF/OTF 字体文件中提取汉字字形的轮廓点序列。

参考: docs/technical-design.md 第 3.1 节
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates


# CJK Unified Ideographs 基本区: U+4E00 - U+9FFF
CJK_UNIFIED_START = 0x4E00
CJK_UNIFIED_END = 0x9FFF

# CJK Unified Ideographs 扩展区B: U+20000 - U+2A6DF (常用字部分)
CJK_EXTENSION_B_START = 0x20000
CJK_EXTENSION_B_END = 0x2A6DF

# CJK 兼容区 (部分常用字)
CJK_COMPAT_START = 0xF900
CJK_COMPAT_END = 0xFAFF


@dataclass
class ContourPoint:
    """
    轮廓上的一个点。

    Attributes:
        x: X 坐标 (F26Dot6 定点数，通常需要 / 64 获取浮点值)
        y: Y 坐标 (F26Dot6 定点数，通常需要 / 64 获取浮点值)
        is_on_curve: 是否在曲线上。True=在线上，False=为控制点
    """

    x: int
    y: int
    is_on_curve: bool

    def to_float(self) -> tuple[float, float]:
        """转换为浮点坐标"""
        return (self.x / 64.0, self.y / 64.0)

    def __repr__(self) -> str:
        flag = "O" if self.is_on_curve else "C"
        return f"ContourPoint({self.x}, {self.y}, {flag})"


@dataclass
class Contour:
    """
    一个闭合轮廓，由一系列点组成。

    轮廓是字形边界的一条闭合曲线，可能是外轮廓或内轮廓（洞）。
    """

    points: list[ContourPoint] = field(default_factory=list)

    def add_point(self, x: int, y: int, is_on_curve: bool) -> None:
        """添加一个点"""
        self.points.append(ContourPoint(x, y, is_on_curve))

    def is_empty(self) -> bool:
        """检查轮廓是否为空"""
        return len(self.points) == 0

    def __iter__(self):
        return iter(self.points)

    def __getitem__(self, index: int) -> ContourPoint:
        return self.points[index]

    def __len__(self) -> int:
        return len(self.points)


@dataclass
class GlyphContours:
    """
    一个字形的轮廓数据。

    包含该字形的 Unicode 编码、所有轮廓点序列、以及包围盒。

    Attributes:
        unicode: 字符的 Unicode 编码 (0 表示无名glyph)
        contours: 轮廓列表
        bbox: 包围盒 (xMin, yMin, xMax, yMax)，单位为 F26Dot6
        name: 字形名称 (可选)
    """

    unicode: int
    contours: list[Contour] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = field(default_factory=lambda: (0, 0, 0, 0))
    name: str = ""

    def add_contour(self) -> Contour:
        """开始一个新的轮廓，返回该轮廓对象"""
        contour = Contour()
        self.contours.append(contour)
        return contour

    def get_point_count(self) -> int:
        """获取总点数"""
        return sum(len(c) for c in self.contours)

    def is_empty(self) -> bool:
        """检查是否有轮廓数据"""
        return len(self.contours) == 0 or all(c.is_empty() for c in self.contours)

    def recompute_bbox(self) -> None:
        """从当前轮廓重新计算包围盒（F26Dot6 整数）"""
        if not self.contours or self.is_empty():
            self.bbox = (0, 0, 0, 0)
            return
        x_min = y_min = float("inf")
        x_max = y_max = float("-inf")
        for contour in self.contours:
            for p in contour.points:
                px = float(p.x)
                py = float(p.y)
                if px < x_min:
                    x_min = px
                if px > x_max:
                    x_max = px
                if py < y_min:
                    y_min = py
                if py > y_max:
                    y_max = py
        self.bbox = (
            int(x_min),
            int(y_min),
            int(x_max),
            int(y_max),
        )

    def bounding_box(self) -> tuple[float, float, float, float]:
        """获取浮点形式的包围盒"""
        x_min, y_min, x_max, y_max = self.bbox
        return (x_min / 64.0, y_min / 64.0, x_max / 64.0, y_max / 64.0)


@dataclass
class GlyphExtractorConfig:
    """字形提取配置"""

    # 是否只提取 CJK 汉字
    cjk_only: bool = True

    # 是否包含 CJK 扩展区
    include_extension_b: bool = False

    # 是否包含 CJK 兼容区
    include_compat: bool = False

    # 单位 EM 尺寸 (通常 1000 或 2048)
    units_per_em: int = 1000


@dataclass
class GlyphExtractResult:
    """提取结果"""

    glyph: GlyphContours
    success: bool
    error_message: str = ""


class GlyphExtractor:
    """
    TTF/OTF 字体文件字形轮廓提取器。

    使用 fontTools 解析字体文件，提取指定汉字的字形轮廓数据。

    Usage:
        extractor = GlyphExtractor()
        result = extractor.extract_from_file("fonts/simhei.ttf", 0x6C49)
        if result.success:
            glyph = result.glyph
            print(f"Unicode: {glyph.unicode:#x}")
            print(f"Contours: {len(glyph.contours)}")
            print(f"Points: {glyph.get_point_count()}")
    """

    def __init__(self, config: GlyphExtractorConfig | None = None):
        self.config = config or GlyphExtractorConfig()

    def _is_cjk(self, unicode_val: int) -> bool:
        """检查 Unicode 是否为 CJK 汉字范围"""
        if self.config.cjk_only:
            # 基本区
            if CJK_UNIFIED_START <= unicode_val <= CJK_UNIFIED_END:
                return True
            # 扩展区 B
            if self.config.include_extension_b and CJK_EXTENSION_B_START <= unicode_val <= CJK_EXTENSION_B_END:
                return True
            # 兼容区
            if self.config.include_compat and CJK_COMPAT_START <= unicode_val <= CJK_COMPAT_END:
                return True
            return False
        return True

    def _build_cmap(self, font: TTFont) -> dict[int, str]:
        """
        从字体文件构建 Unicode -> glyphName 的映射。

        Returns:
            dict[int, str]: {unicode: glyphName}
        """
        cmap = {}
        for table in font["cmap"].tables:
            if table.isUnicode():
                for unicode_val, glyph_name in table.cmap.items():
                    if self._is_cjk(unicode_val):
                        cmap[unicode_val] = glyph_name
        return cmap

    def extract_from_file(
        self, font_path: str | Path, unicode_val: int
    ) -> GlyphExtractResult:
        """
        从字体文件提取指定 Unicode 字符的字形轮廓。

        Args:
            font_path: 字体文件路径 (.ttf 或 .otf)
            unicode_val: 要提取的 Unicode 编码

        Returns:
            GlyphExtractResult: 提取结果
        """
        try:
            font = TTFont(font_path)
            return self._extract_glyph(font, unicode_val)
        except Exception as e:
            return GlyphExtractResult(
                glyph=GlyphContours(unicode=unicode_val),
                success=False,
                error_message=f"Failed to read font file: {e}",
            )

    def extract_from_stream(
        self, stream: BinaryIO, unicode_val: int
    ) -> GlyphExtractResult:
        """
        从字节流提取指定 Unicode 字符的字形轮廓。

        Args:
            stream: 字体文件字节流
            unicode_val: 要提取的 Unicode 编码

        Returns:
            GlyphExtractResult: 提取结果
        """
        try:
            font = TTFont(stream=stream)
            return self._extract_glyph(font, unicode_val)
        except Exception as e:
            return GlyphExtractResult(
                glyph=GlyphContours(unicode=unicode_val),
                success=False,
                error_message=f"Failed to parse font stream: {e}",
            )

    def _extract_glyph(self, font: TTFont, unicode_val: int) -> GlyphExtractResult:
        """从已加载的 TTFont 对象提取字形"""
        cmap = self._build_cmap(font)

        glyph_name = cmap.get(unicode_val)
        if not glyph_name:
            return GlyphExtractResult(
                glyph=GlyphContours(unicode=unicode_val),
                success=False,
                error_message=f"No glyph found for Unicode U+{unicode_val:04X}",
            )

        glyph_set = font.getGlyphSet()
        if glyph_name not in glyph_set:
            return GlyphExtractResult(
                glyph=GlyphContours(unicode=unicode_val, name=glyph_name),
                success=False,
                error_message=f"Glyph '{glyph_name}' not found in glyphSet",
            )

        # 使用 RecordingPen 提取轮廓
        pen = RecordingPen()
        glyph_set[glyph_name].draw(pen)

        # 构建 GlyphContours
        glyph = GlyphContours(unicode=unicode_val, name=glyph_name)
        current_contour: Contour | None = None

        for op, args in pen.value:
            if op == "moveTo":
                # 新轮廓开始
                current_contour = glyph.add_contour()
                # moveTo 的参数是终点坐标
                if args:
                    x, y = args[0]
                    current_contour.add_point(x, y, True)
            elif op == "lineTo":
                if current_contour is None:
                    current_contour = glyph.add_contour()
                # 直线到下一个点
                if args:
                    x, y = args[0]
                    current_contour.add_point(x, y, True)
            elif op == "qCurveTo":
                # 二次贝塞尔曲线
                # args 是控制点列表 + 终点
                if current_contour is None:
                    current_contour = glyph.add_contour()
                for pt in args:
                    x, y = pt
                    current_contour.add_point(x, y, False)
            elif op == "curveTo":
                # 三次贝塞尔曲线
                if current_contour is None:
                    current_contour = glyph.add_contour()
                for pt in args:
                    x, y = pt
                    current_contour.add_point(x, y, False)
            elif op == "closePath":
                # 轮廓闭合
                pass
            elif op == "endPath":
                # 路径结束
                current_contour = None

        # 获取包围盒
        glyf_table = font.get("glyf")
        if glyf_table:
            glyph_table = glyf_table.glyphs.get(glyph_name)
            if glyph_table and hasattr(glyph_table, "xMin"):
                glyph.bbox = (
                    glyph_table.xMin,
                    glyph_table.yMin,
                    glyph_table.xMax,
                    glyph_table.yMax,
                )

        return GlyphExtractResult(glyph=glyph, success=True)

    def extract_all_cjk(self, font_path: str | Path) -> Iterator[GlyphExtractResult]:
        """
        提取字体文件中所有 CJK 汉字的字形轮廓。

        Args:
            font_path: 字体文件路径

        Yields:
            GlyphExtractResult: 每个 CJK 汉字的提取结果
        """
        try:
            font = TTFont(font_path)
            cmap = self._build_cmap(font)

            for unicode_val in sorted(cmap.keys()):
                result = self._extract_glyph(font, unicode_val)
                yield result
        except Exception as e:
            yield GlyphExtractResult(
                glyph=GlyphContours(unicode=0),
                success=False,
                error_message=f"Failed to read font file: {e}",
            )

    def extract_batch(
        self, font_path: str | Path, unicode_vals: Sequence[int]
    ) -> list[GlyphExtractResult]:
        """
        批量提取多个字符的字形轮廓。

        Args:
            font_path: 字体文件路径
            unicode_vals: Unicode 编码列表

        Returns:
            list[GlyphExtractResult]: 提取结果列表
        """
        results = []
        for uv in unicode_vals:
            result = self.extract_from_file(font_path, uv)
            results.append(result)
        return results
