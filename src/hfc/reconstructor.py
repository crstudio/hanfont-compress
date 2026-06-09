"""
字形重建器。

将 EncodedChar 还原成可渲染的 GlyphContours:
    - COMPONENT 模式: 取出部件的轮廓样本 → 应用 PartInstance.transform
    - RAW 模式:    直接使用 raw_contours

这是"原始字形 vs 压缩重建字形"对比渲染的核心模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .component_library import Component, ComponentLibrary
from .component_matcher import EncodedChar, PartInstance
from .glyph_extractor import Contour, ContourPoint, GlyphContours


@dataclass
class ReconstructResult:
    """重建结果"""

    glyph: GlyphContours
    mode: str  # "COMPONENT" | "RAW"
    component_ids: list[str]
    similarity: float = 1.0


class GlyphReconstructor:
    """
    基于部件库的字形重建器。

    用法::

        recon = GlyphReconstructor(library)
        result = recon.reconstruct(encoded_char)
        glyph = result.glyph   # 可直接交给 renderer 渲染
    """

    def __init__(self, library: ComponentLibrary):
        self.library = library

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def reconstruct(self, encoded: EncodedChar) -> ReconstructResult:
        """
        从 EncodedChar 重建 GlyphContours。

        Args:
            encoded: 由 ComponentMatcher 产生的编码结果

        Returns:
            ReconstructResult, 包含重建后的 GlyphContours
        """
        if encoded.mode == "COMPONENT":
            return self._reconstruct_component_mode(encoded)
        return self._reconstruct_raw_mode(encoded)

    def reconstruct_from_parts(
        self,
        unicode_val: int,
        parts: list[PartInstance],
    ) -> GlyphContours:
        """
        低阶: 直接从 PartInstance 列表重建字形。

        Args:
            unicode_val: 目标字形的 Unicode
            parts: 部件实例列表（每个部件 + 变换）
        """
        glyph = GlyphContours(unicode=unicode_val)
        for part in parts:
            comp = self.library.get_component(part.component_id)
            if comp is None or comp.num_samples() == 0:
                continue
            sample_contours = comp.contour_samples[0]
            transformed = self._apply_transform(
                sample_contours, part.transform
            )
            for contour in transformed:
                glyph.contours.append(contour)
        glyph.recompute_bbox()
        return glyph

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _reconstruct_component_mode(
        self, encoded: EncodedChar
    ) -> ReconstructResult:
        """COMPONENT 模式: 部件 + 仿射变换"""
        glyph = GlyphContours(unicode=encoded.unicode)
        ids: list[str] = []
        avg_sim = 0.0

        for part in encoded.parts:
            comp = self.library.get_component(part.component_id)
            if comp is None or comp.num_samples() == 0:
                # 部件库中缺失，跳过（也可改走 RAW 回退）
                continue

            ids.append(part.component_id)
            avg_sim += part.similarity

            # 如果部件提供了轮廓差分修正，则优先使用修正后的轮廓
            source = (
                part.contour_override
                if part.contour_override is not None
                else comp.contour_samples[0]
            )
            transformed = self._apply_transform(source, part.transform)
            for contour in transformed:
                glyph.contours.append(contour)

        glyph.recompute_bbox()
        if ids:
            avg_sim /= len(ids)

        return ReconstructResult(
            glyph=glyph,
            mode="COMPONENT",
            component_ids=ids,
            similarity=avg_sim,
        )

    def _reconstruct_raw_mode(
        self, encoded: EncodedChar
    ) -> ReconstructResult:
        """RAW 模式: 原样返回存储的轮廓"""
        glyph = GlyphContours(unicode=encoded.unicode)
        if encoded.raw_contours:
            for contour in encoded.raw_contours:
                glyph.contours.append(contour)
            glyph.recompute_bbox()
        return ReconstructResult(
            glyph=glyph,
            mode="RAW",
            component_ids=[],
            similarity=0.0,
        )

    @staticmethod
    def _apply_transform(
        contours: list[Contour], transform: object,
    ) -> list[Contour]:
        """
        对一组轮廓应用仿射变换。

        transform 必须具备 a, b, c, d, tx, ty 字段。
            x' = a*x + b*y + tx
            y' = c*x + d*y + ty
        """
        a = getattr(transform, "a", 1.0)
        b = getattr(transform, "b", 0.0)
        c = getattr(transform, "c", 0.0)
        d = getattr(transform, "d", 1.0)
        tx = getattr(transform, "tx", 0.0)
        ty = getattr(transform, "ty", 0.0)

        identity = (
            abs(a - 1.0) < 1e-9 and abs(d - 1.0) < 1e-9
            and abs(b) < 1e-9 and abs(c) < 1e-9
            and abs(tx) < 1e-9 and abs(ty) < 1e-9
        )
        if identity:
            return contours  # 恒等变换，直接返回原列表 (不修改)

        new_contours: list[Contour] = []
        for contour in contours:
            nc = Contour()
            for p in contour.points:
                x_new = a * p.x + b * p.y + tx
                y_new = c * p.x + d * p.y + ty
                nc.add_point(x_new, y_new, p.is_on_curve)
            new_contours.append(nc)
        return new_contours
