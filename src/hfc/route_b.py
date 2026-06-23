"""
路线B: 带 Delta 近无损压缩。

  - 基于路线A的匹配结果
  - 额外存储"原始轮廓 - 重建轮廓"的 Delta 数据
  - 若 Delta 成本过高，则退化到 RAW

Delta 类型：
  - point-offset: 每个原始点与重建点的 (dx, dy) 偏移
  - extra-contour: 额外补充的小轮廓（小钩/小点）
  - replace-contour: 局部替换某些轮廓段

成本判断：
  reuseCost = componentIdCost + transformCost + deltaCost
  rawCost   = originalContourCost
  if reuseCost < rawCost: 使用部件复用
  else: 保存 raw contour

参考: docs/try.md 第 6.8-6.15 节（路线 B）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .route_a import (
    CompressedGlyph,
    MatchedComponent,
    RouteAConfig,
    RouteAEncoder,
    RouteAResult,
    Transform2D,
)

if TYPE_CHECKING:
    from .glyph_extractor import GlyphContours


# ============================================================================
# 1. 配置
# ============================================================================


@dataclass
class RouteBConfig(RouteAConfig):
    """路线B（带Delta近无损）配置，在 RouteA 基础上增加 Delta 参数。"""

    delta_mode: Literal["point-offset", "extra-contour", "replace-contour"] = (
        "point-offset"
    )
    quality: Literal["lossy", "near-lossless", "lossless"] = "near-lossless"
    max_delta_points: int = 50  # Delta 点数超过此值则走 raw
    delta_point_ratio: float = 0.5  # Delta 点数 / 原始点数 < 此比值才采用 Delta


# ============================================================================
# 2. Delta 数据结构
# ============================================================================


@dataclass
class DeltaData:
    """
    一个子轮廓的 Delta 数据。

    mode=point-offset: 每个原始点与重建点的 (dx, dy) 偏移
    mode=extra-contour: 额外补充的小轮廓数据
    mode=replace-contour: 替换某些轮廓段
    """

    mode: str = "point-offset"
    points: list[tuple[float, float]] = field(default_factory=list)
    extra_contours: list = field(default_factory=list)  # list[Contour]
    replaced_segments: list = field(default_factory=list)


@dataclass
class DeltaResult:
    """一个字形经过 Delta 评估后的决策结果。"""

    mode: Literal["DELTA", "RAW"]  # DELTA=值得加Delta, RAW=不划算退化
    delta_data: DeltaData | None
    delta_points_estimate: int  # Delta 点数估算
    original_points: int         # 原始点数


# ============================================================================
# 3. Delta 成本评估
# ============================================================================


def evaluate_delta_cost(
    original_glyph: "GlyphContours",
    reconstructed_contours: list,
    config: RouteBConfig,
) -> DeltaResult:
    """
    判断对某个字形是否值得加 Delta。

    流程:
      1. 统计原始轮廓点数
      2. 估算 Delta 数据量（点数当量）
      3. 若 delta_points < original_points * delta_point_ratio → 划算，返回 DELTA
      4. 否则 → 不划算，退化到 RAW
    """
    if not original_glyph.contours:
        return DeltaResult(mode="RAW", delta_data=None,
                           delta_points_estimate=0, original_points=0)

    original_points = sum(len(c) for c in original_glyph.contours)
    reconstructed_points = sum(len(c) for c in reconstructed_contours)

    if config.delta_mode == "point-offset":
        # point-offset: Delta 点数 = min(reconstructed_points, max_delta_points)
        delta_est = min(reconstructed_points, config.max_delta_points)
    elif config.delta_mode == "extra-contour":
        # extra-contour: Delta = 超出比例的轮廓点数
        delta_est = max(0, reconstructed_points - original_points)
    else:
        # replace-contour: Delta = 两个轮廓的点数差
        delta_est = abs(reconstructed_points - original_points)

    if delta_est < original_points * config.delta_point_ratio:
        delta_data = DeltaData(
            mode=config.delta_mode,
            points=[
                (0.0, 0.0) for _ in range(delta_est)
            ],  # 占位：真实实现中记录实际偏移
        )
        return DeltaResult(
            mode="DELTA",
            delta_data=delta_data,
            delta_points_estimate=delta_est,
            original_points=original_points,
        )
    else:
        return DeltaResult(
            mode="RAW",
            delta_data=None,
            delta_points_estimate=delta_est,
            original_points=original_points,
        )


# ============================================================================
# 4. 路线B 结果
# ============================================================================


@dataclass
class RouteBResult(RouteAResult):
    """路线B结果，增加 Delta 相关统计。"""

    delta_mode_chars: int = 0  # 采用 Delta 的字数
    delta_bytes_estimate: int = 0  # 粗略 Delta 数据量（点数当量）

    def summary(self) -> str:
        base = super().summary()
        extra = [
            f"采用 Delta 的字数: {self.delta_mode_chars}",
            f"Delta 数据估算: {self.delta_bytes_estimate} 点",
        ]
        return base + "\n" + "\n".join(extra)


# ============================================================================
# 5. 路线B 编码器
# ============================================================================


class RouteBEncoder(RouteAEncoder):
    """
    路线B：在路线A基础上增加 Delta 数据以实现近无损。

    流程:
      1. 先运行 RouteA 的 encode 得到初步结果
      2. 对每个字的每个 component reference，做"原始 vs 重建"对比
      3. 计算 point-offset delta
      4. 若 delta 点数/字节 < 原始的 threshold，则记录 delta；否则退化到 RAW
    """

    def __init__(self, config: RouteBConfig | None = None):
        super().__init__(config)
        self.config_b: RouteBConfig = config or RouteBConfig()

    def encode(self, font_glyphs: list,
               progress_callback: Any = None) -> RouteBResult:
        """先做路线A，再做 Delta 增强。progress_callback(current, total) 可选。"""
        total = len(font_glyphs)
        base = super().encode(font_glyphs, progress_callback=progress_callback)
        result = RouteBResult(
            total_chars=base.total_chars,
            component_mode_chars=base.component_mode_chars,
            raw_mode_chars=base.raw_mode_chars,
            total_components=base.total_components,
            component_dictionary=base.component_dictionary,
            compressed_glyphs=list(base.compressed_glyphs),
            stats=dict(base.stats),
        )

        delta_points_total = 0
        delta_adopted_chars = 0

        for idx, cg in enumerate(result.compressed_glyphs):
            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback(total + idx + 1, total * 2)
            if cg.mode != "COMPONENT" or not cg.components:
                continue

            glyph = font_glyphs[idx] if idx < len(font_glyphs) else None
            if glyph is None or not glyph.contours:
                continue

            # 模拟重建
            reconstructed: list = []
            for m in cg.components:
                comp_sub = result.component_dictionary.get(m.component_id)
                if comp_sub is None:
                    continue
                warped = m.transform.apply_subcontour(comp_sub)
                reconstructed.extend(warped.contours)
            reconstructed.extend(cg.raw_contours)

            # 评估 delta 成本
            delta_result = evaluate_delta_cost(glyph, reconstructed, self.config_b)
            if delta_result.mode == "DELTA":
                result.delta_mode_chars += 1
                delta_points_total += delta_result.delta_points_estimate
                delta_adopted_chars += 1
            else:
                # 不划算：退化到 RAW
                result.component_mode_chars -= 1
                result.raw_mode_chars += 1
                result.total_components -= len(cg.components)
                cg.mode = "RAW"
                cg.raw_contours = list(glyph.contours)
                cg.components = []

        result.delta_bytes_estimate = delta_points_total
        result.stats["delta_adopted_chars"] = delta_adopted_chars
        result.stats["delta_points_total"] = delta_points_total
        return result

    def decode(self, result: RouteBResult) -> dict[int, list]:
        """解码（与 RouteA 相同，真实实现会把 Delta 加回去）。"""
        return super().decode(result)
