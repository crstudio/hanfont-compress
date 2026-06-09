"""
模块3: 部件匹配编码器 (ComponentMatcher)

将汉字字形与部件库中的部件进行匹配，将汉字编码为 "部件引用 + 变换参数" 的形式。

核心算法:
1. 轮廓点归一化 (Procrustes Analysis)
   - 平移: 将两个点集的中心对齐到原点
   - 缩放: 归一化到单位方差
   - 旋转: 找到最优旋转角使两堆点距离最小

2. 轮廓相似度度量 (Hausdorff Distance)
   - 计算两个点集之间的最大最小距离
   - 返回 0-1 的相似度分数

3. 仿射变换拟合 (最小二乘法)
   - 从部件轮廓点集映射到字形目标区域
   - 求解 2x3 仿射矩阵 [a b tx; c d ty]

编码结果:
- mode = "COMPONENT": 成功编码为部件引用
- mode = "RAW": 匹配失败，降级为原始轮廓存储

参考: docs/technical-design.md 第 3.3 节 (EncodedChar / Transform)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .component_library import Component, ComponentLibrary
from .glyph_extractor import Contour, ContourPoint, GlyphContours


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class Transform:
    """
    2D 仿射变换矩阵。

    y = A * x + t, 其中:
        A = [[a b],
             [c d]]  2x2 线性变换
        t = [tx, ty]^T  2x1 平移向量

    Attributes:
        a, b, c, d: 线性变换矩阵元素
        tx, ty: 平移量 (以 F26Dot6 为单位, 除以64得到字体单位)
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        """应用变换: (x, y) -> (a*x + b*y + tx, c*x + d*y + ty)"""
        return (
            self.a * x + self.b * y + self.tx,
            self.c * x + self.d * y + self.ty,
        )

    def apply_to_points(
        self, points: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        """批量应用变换"""
        return [self.apply(x, y) for x, y in points]

    def inverse(self) -> "Transform":
        """计算逆变换"""
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-10:
            return Transform()  # 奇异，返回恒等

        inv_a = self.d / det
        inv_b = -self.b / det
        inv_c = -self.c / det
        inv_d = self.a / det
        inv_tx = -(inv_a * self.tx + inv_b * self.ty)
        inv_ty = -(inv_c * self.tx + inv_d * self.ty)

        return Transform(inv_a, inv_b, inv_c, inv_d, inv_tx, inv_ty)

    def to_matrix(self) -> np.ndarray:
        """转换为 3x3 齐次坐标矩阵"""
        return np.array([
            [self.a, self.b, self.tx],
            [self.c, self.d, self.ty],
            [0.0, 0.0, 1.0],
        ])

    @classmethod
    def identity(cls) -> "Transform":
        """返回恒等变换"""
        return cls()

    @classmethod
    def from_scale(cls, sx: float, sy: float) -> "Transform":
        """从缩放创建变换"""
        return cls(a=sx, b=0.0, c=0.0, d=sy, tx=0.0, ty=0.0)

    @classmethod
    def from_translation(cls, tx: float, ty: float) -> "Transform":
        """从平移创建变换"""
        return cls(a=1.0, b=0.0, c=0.0, d=1.0, tx=tx, ty=ty)


@dataclass
class PartInstance:
    """
    汉字中的一个部件实例。

    Attributes:
        component_id: 引用的部件 ID
        transform: 将部件轮廓变换到字形坐标的参数
        similarity: 匹配相似度 (0-1)
        contour_override: 可选的轮廓差分修正 (当相似度不够完美时用)
    """

    component_id: str
    transform: Transform
    similarity: float = 1.0
    contour_override: Optional[list[Contour]] = None

    def summary(self) -> str:
        """返回简短摘要"""
        return (
            f"PartInstance(component_id={self.component_id}, "
            f"similarity={self.similarity:.3f}, "
            f"transform=[{self.a:.2f} {self.b:.2f} tx={self.tx:.1f}; "
            f"{self.c:.2f} {self.d:.2f} ty={self.ty:.1f}])"
        )


@dataclass
class EncodedChar:
    """
    一个汉字的编码结果。

    Attributes:
        unicode: 字符的 Unicode 编码
        mode: "COMPONENT" = 部件编码成功
              "RAW" = 降级为原始轮廓存储
        parts: 组成这个字的部件实例列表 (mode=COMPONENT 时有效)
        raw_contours: 原始轮廓 (mode=RAW 时使用)
        match_score: 整体匹配得分 (mode=COMPONENT 时)
        manual_review_needed: 是否需要人工审核
    """

    unicode: int
    mode: str = "RAW"  # "COMPONENT" 或 "RAW"
    parts: list[PartInstance] = field(default_factory=list)
    raw_contours: Optional[list[Contour]] = None
    match_score: float = 0.0
    manual_review_needed: bool = False

    def is_component_mode(self) -> bool:
        return self.mode == "COMPONENT"

    def summary(self) -> str:
        """返回编码摘要"""
        char_repr = chr(self.unicode) if 0 < self.unicode < 0x110000 else ""
        if self.is_component_mode():
            part_ids = [p.component_id for p in self.parts]
            return (
                f"U+{self.unicode:04X}{char_repr}: "
                f"mode=COMPONENT, score={self.match_score:.3f}, "
                f"parts=[{', '.join(part_ids[:3])}"
                f"{'...' if len(part_ids) > 3 else ''}]"
            )
        else:
            return f"U+{self.unicode:04X}{char_repr}: mode=RAW"


# ============================================================================
# 匹配配置
# ============================================================================

@dataclass
class MatchConfig:
    """
    匹配算法配置。

    Attributes:
        similarity_threshold: 相似度阈值，低于此值的匹配被拒绝 (0-1)
        max_scale_deviation: 允许的缩放偏离 1.0 的最大幅度
        max_rotation_deg: 允许的最大旋转角度（度）
        max_shear: 允许的最大切变系数
        min_points_for_match: 参与匹配的最少点数
        use_all_samples: 是否尝试部件的所有形态样本
        auto_review_threshold: 低于此相似度自动标记需人工审核
        min_components: 最少匹配部件数才判定为 COMPONENT 模式
    """

    similarity_threshold: float = 0.75
    max_scale_deviation: float = 0.5
    max_rotation_deg: float = 15.0
    max_shear: float = 0.3
    min_points_for_match: int = 4
    use_all_samples: bool = True
    auto_review_threshold: float = 0.85
    min_components: int = 1


# ============================================================================
# 几何算法辅助函数
# ============================================================================

def _contours_to_points(contours: list[Contour]) -> np.ndarray:
    """
    将轮廓列表转换为 (N, 2) 的浮点数组。
    同时将 F26Dot6 坐标转换为浮点。
    """
    all_points: list[tuple[float, float]] = []
    for contour in contours:
        for pt in contour:
            all_points.append((pt.x / 64.0, pt.y / 64.0))
    if not all_points:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array(all_points, dtype=np.float64)


def _normalize_points(points: np.ndarray) -> tuple[np.ndarray, tuple[float, float], float]:
    """
    点集归一化: 平移到中心 -> 缩放到单位方差。

    Returns:
        (normalized_points, center, scale)
    """
    if points.shape[0] == 0:
        return points, (0.0, 0.0), 1.0

    center = points.mean(axis=0)
    centered = points - center

    scale = np.sqrt((centered ** 2).sum() / max(len(centered), 1))
    if scale < 1e-10:
        scale = 1.0

    normalized = centered / scale
    return normalized, (center[0], center[1]), scale


def procrustes_align(
    source: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, float]:
    """
    简化版 Procrustes 对齐:
    - 将 source 对齐到 target (两者点数可以不同)
    - 先归一化，再用最近点距离总和最小化来衡量相似度

    Returns:
        (aligned_source, similarity_score)
        similarity_score 在 0-1 之间，1 表示完全相同
    """
    if source.shape[0] < 3 or target.shape[0] < 3:
        return source, 0.0

    # 归一化两个点集
    src_norm, src_center, src_scale = _normalize_points(source)
    tgt_norm, tgt_center, tgt_scale = _normalize_points(target)

    # 计算 Hausdorff-like 相似度
    similarity = _compute_points_similarity(src_norm, tgt_norm)

    return src_norm, similarity


def _compute_points_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    计算两个归一化点集的相似度。

    使用双向最近点平均距离，映射到 0-1 分数。

    Returns:
        0-1 的相似度分数，1 表示完全一致
    """
    if a.shape[0] == 0 or b.shape[0] == 0:
        return 0.0

    # 对 a 中每个点找 b 中最近点
    distances_a_to_b = _min_distances(a, b)
    # 对 b 中每个点找 a 中最近点
    distances_b_to_a = _min_distances(b, a)

    # 取双向平均的平均
    avg_dist = float(
        (distances_a_to_b.mean() + distances_b_to_a.mean()) / 2.0
    )

    # 将距离映射为相似度: 平均距离越小，相似度越高
    # 经验映射: similarity = exp(-avg_dist * k)
    # k 调参: 当 avg_dist = 0.5 → similarity ≈ 0.37
    k = 3.0
    similarity = float(np.exp(-avg_dist * k))

    return max(0.0, min(1.0, similarity))


def _min_distances(from_pts: np.ndarray, to_pts: np.ndarray) -> np.ndarray:
    """
    对 from_pts 中每个点找到 to_pts 中最近点的距离。

    Returns:
        shape (len(from_pts),) 的距离数组
    """
    # 暴力 O(n*m) 实现（对我们的规模足够）
    # from_pts: (n, 2), to_pts: (m, 2)
    n = from_pts.shape[0]
    m = to_pts.shape[0]

    # 用广播计算所有对的距离
    diff = from_pts[:, np.newaxis, :] - to_pts[np.newaxis, :, :]  # (n, m, 2)
    distances = np.sqrt((diff ** 2).sum(axis=2))  # (n, m)
    min_dists = distances.min(axis=1)  # (n,)

    return min_dists


def fit_affine_transform(
    source: np.ndarray, target: np.ndarray
) -> tuple[Transform, float]:
    """
    用最小二乘法拟合从 source 到 target 的仿射变换。

    假设 target[i] ≈ A * source[i] + t

    返回:
        (Transform, 平均拟合误差)
    """
    n = min(source.shape[0], target.shape[0])
    if n < 3:
        return Transform.identity(), float("inf")

    # 为了稳定，取最少的点数
    s = source[:n]
    t = target[:n]

    # 构建线性方程组
    # 对每个点:
    #   a*x_i + b*y_i + tx = x'_i
    #   c*x_i + d*y_i + ty = y'_i
    #
    # A * params = b
    #
    # params = [a, b, tx, c, d, ty]^T
    #
    # 这可以拆成两个独立的 3xN 子问题
    # 解 [a, b, tx]: [x_i, y_i, 1] * [a; b; tx] = x'_i
    # 解 [c, d, ty]: [x_i, y_i, 1] * [c; d; ty] = y'_i

    M = np.column_stack([s, np.ones(n)])  # (n, 3)

    # 用最小二乘求解两套参数
    try:
        params_x, _, _, _ = np.linalg.lstsq(M, t[:, 0], rcond=None)
        params_y, _, _, _ = np.linalg.lstsq(M, t[:, 1], rcond=None)
    except np.linalg.LinAlgError:
        return Transform.identity(), float("inf")

    a, b, tx = params_x
    c, d, ty = params_y

    transform = Transform(
        a=float(a), b=float(b), c=float(c), d=float(d),
        tx=float(tx), ty=float(ty),
    )

    # 计算拟合误差
    predicted = np.column_stack([
        a * s[:, 0] + b * s[:, 1] + tx,
        c * s[:, 0] + d * s[:, 1] + ty,
    ])
    avg_error = float(np.sqrt(((predicted - t) ** 2).sum() / n))

    return transform, avg_error


def _validate_transform(transform: Transform, config: MatchConfig) -> bool:
    """
    验证变换参数是否在合理范围内。

    检查:
    - 缩放偏离 1.0 的幅度
    - 旋转角度（从矩阵的 skew 部分估计）
    - 切变
    """
    # 估计缩放: sqrt(abs(a*d - b*c)) = det 的平方根
    det = transform.a * transform.d - transform.b * transform.c
    scale = float(np.sqrt(abs(det))) if det != 0 else 1.0

    # 缩放偏离检查
    if abs(scale - 1.0) > config.max_scale_deviation:
        return False

    # 估计旋转 (假设是纯旋转+缩放)
    # 对非退化矩阵，旋转角 = atan2(c, a)
    if abs(transform.a) > 1e-10:
        rotation = float(np.arctan2(transform.c, transform.a)) * 180.0 / np.pi
        # 检查是否接近纯旋转（b 和 -c 应大致相等）
        skew = abs(transform.b + transform.c) / max(1.0, abs(transform.a) + abs(transform.d))
        if skew > config.max_shear:
            return False
    else:
        rotation = 0.0

    # 归一化到 [-180, 180] 后检查
    rotation = ((rotation + 180.0) % 360.0) - 180.0
    if abs(rotation) > config.max_rotation_deg:
        return False

    return True


# ============================================================================
# 部件匹配器主类
# ============================================================================

class ComponentMatcher:
    """
    将字形轮廓与部件库进行匹配，产出 EncodedChar 编码。

    Usage:
        matcher = ComponentMatcher(library)
        result = matcher.match(glyph)
        print(result.summary())
    """

    def __init__(
        self,
        library: ComponentLibrary,
        config: Optional[MatchConfig] = None,
    ):
        """
        Args:
            library: 部件库
            config: 匹配配置，None 时使用默认值
        """
        self.library = library
        self.config = config or MatchConfig()
        self._stats_total = 0
        self._stats_component_mode = 0
        self._stats_raw_mode = 0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def match(self, glyph: GlyphContours) -> EncodedChar:
        """
        匹配单个字形。

        Args:
            glyph: 字形轮廓数据

        Returns:
            EncodedChar: 编码结果
        """
        self._stats_total += 1

        if glyph.is_empty():
            encoded = EncodedChar(unicode=glyph.unicode, mode="RAW")
            encoded.raw_contours = glyph.contours
            self._stats_raw_mode += 1
            return encoded

        # 提取字形的点集
        glyph_points = _contours_to_points(glyph.contours)

        if glyph_points.shape[0] < self.config.min_points_for_match:
            encoded = EncodedChar(unicode=glyph.unicode, mode="RAW")
            encoded.raw_contours = glyph.contours
            self._stats_raw_mode += 1
            return encoded

        # 遍历部件库，寻找最佳匹配部件
        best_result = self._find_best_match(glyph_points, glyph)

        if best_result is None:
            # 没找到，降级为 RAW
            encoded = EncodedChar(unicode=glyph.unicode, mode="RAW")
            encoded.raw_contours = glyph.contours
            self._stats_raw_mode += 1
            return encoded

        part, similarity, transform = best_result

        if similarity < self.config.similarity_threshold:
            # 相似度不够，降级
            encoded = EncodedChar(unicode=glyph.unicode, mode="RAW")
            encoded.raw_contours = glyph.contours
            encoded.match_score = similarity
            self._stats_raw_mode += 1
            return encoded

        # 成功: 构建 COMPONENT 模式
        encoded = EncodedChar(
            unicode=glyph.unicode,
            mode="COMPONENT",
            match_score=similarity,
        )
        encoded.parts = [
            PartInstance(
                component_id=part.id,
                transform=transform,
                similarity=similarity,
            )
        ]

        # 自动审核标记
        if similarity < self.config.auto_review_threshold:
            encoded.manual_review_needed = True

        self._stats_component_mode += 1
        return encoded

    def match_batch(
        self, glyphs: list[GlyphContours]
    ) -> list[EncodedChar]:
        """批量匹配多个字形"""
        return [self.match(g) for g in glyphs]

    def stats(self) -> dict:
        """返回匹配统计信息"""
        total = max(self._stats_total, 1)
        return {
            "total": self._stats_total,
            "component_mode": self._stats_component_mode,
            "raw_mode": self._stats_raw_mode,
            "component_rate": self._stats_component_mode / total,
            "raw_rate": self._stats_raw_mode / total,
        }

    def reset_stats(self) -> None:
        """重置统计"""
        self._stats_total = 0
        self._stats_component_mode = 0
        self._stats_raw_mode = 0

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_best_match(
        self, glyph_points: np.ndarray, glyph: GlyphContours
    ) -> Optional[tuple[Component, float, Transform]]:
        """
        在部件库中寻找与给定字形最匹配的部件。

        Returns:
            (component, similarity, transform) 或 None (无匹配)
        """
        candidates: list[tuple[float, Component, Transform]] = []

        # 遍历所有有样本的部件
        for comp in self.library.components_with_samples():
            for sample_idx, sample_contours in enumerate(comp.contour_samples):
                if not sample_contours:
                    continue

                sample_points = _contours_to_points(sample_contours)
                if sample_points.shape[0] < self.config.min_points_for_match:
                    continue

                # 对齐 + 相似度
                aligned, similarity = procrustes_align(sample_points, glyph_points)

                # 拟合仿射变换
                if sample_points.shape[0] > 0 and glyph_points.shape[0] > 0:
                    # 简化: 直接用 sample_points -> glyph_points 拟合
                    n = min(sample_points.shape[0], glyph_points.shape[0])
                    transform, err = fit_affine_transform(
                        sample_points[:n], glyph_points[:n]
                    )

                    # 验证参数合理性
                    if _validate_transform(transform, self.config):
                        candidates.append((similarity, comp, transform))

                # 只试第一个样本（除非启用 use_all_samples）
                if not self.config.use_all_samples:
                    break

        if not candidates:
            return None

        # 选相似度最高的
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_sim, best_comp, best_transform = candidates[0]
        return best_comp, best_sim, best_transform


# ============================================================================
# 便捷函数
# ============================================================================

def match_glyph_with_library(
    glyph: GlyphContours,
    library: ComponentLibrary,
    threshold: float = 0.75,
) -> EncodedChar:
    """便捷函数: 用给定部件库匹配一个字形"""
    config = MatchConfig(similarity_threshold=threshold)
    matcher = ComponentMatcher(library, config=config)
    return matcher.match(glyph)
