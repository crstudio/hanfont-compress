"""
路线A: 无 Delta 有损压缩。

  - 将字形分解为子轮廓（连通域）
  - 用位图 IoU 在部件库中做匹配
  - 匹配成功：存储 component_id + 仿射变换
  - 匹配失败：添加到部件库作为新部件（或走 RAW fallback）

关键数据结构：
  - Transform2D:      6 参数仿射变换 [x' = a*x + c*y + e; y' = b*x + d*y + f]
  - MatchedComponent: 一个部件的匹配实例（component_id + transform + score）
  - CompressedGlyph:  一个字的压缩结果（COMPONENT 模式 或 RAW 模式）
  - RouteAResult:     整批压缩的产物 + 统计信息

参考: docs/try.md 第 6.1-6.7 节（路线 A）
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

if TYPE_CHECKING:
    from .decomposer import SubContour
    from .glyph_extractor import Contour, ContourPoint, GlyphContours

# ============================================================================
# 1. 配置
# ============================================================================


@dataclass
class RouteAConfig:
    """路线A（无Delta有损压缩）匹配配置。"""

    bitmap_size: int = 128  # 栅格化分辨率
    iou_threshold: float = 0.92  # 匹配成功的 IoU 阈值
    allow_scale_x: bool = True
    allow_scale_y: bool = True
    allow_rotation: bool = False
    max_rotation_deg: float = 3.0
    min_component_size: int = 200  # 字体单位。太小的子轮廓不纳入匹配，走 raw
    seed_chars: int = 3  # 前 N 个字的子轮廓直接作为初始部件库


# ============================================================================
# 2. 仿射变换
# ============================================================================


@dataclass
class Transform2D:
    """
    2D 仿射变换：
        x' = a*x + c*y + e
        y' = b*x + d*y + f

    矩阵形式:
        [ a  c  e ]
        [ b  d  f ]
        [ 0  0  1 ]
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, point: tuple[float, float]) -> tuple[float, float]:
        """对一个 (x, y) 点做仿射变换，返回 (x', y')。"""
        x, y = point
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def apply_contour(self, contour) -> "Contour":
        """对整个轮廓做仿射变换，返回新 Contour（is_on_curve 保持不变）。"""
        from .glyph_extractor import Contour, ContourPoint
        new_points: list[ContourPoint] = []
        for p in contour.points:
            nx, ny = self.apply((float(p.x), float(p.y)))
            new_points.append(ContourPoint(
                x=int(round(nx)),
                y=int(round(ny)),
                is_on_curve=p.is_on_curve,
            ))
        return Contour(points=new_points)

    def apply_subcontour(self, sub: "SubContour") -> "SubContour":
        """对整个 SubContour 做仿射变换。"""
        from .decomposer import SubContour
        new_contours = [self.apply_contour(c) for c in sub.contours]
        result = SubContour(contours=new_contours)
        result.recompute_bbox()
        return result

    def to_simple(self) -> tuple[float, float, float, float, float]:
        """近似为 (tx, ty, sx, sy, rot_rad)。"""
        tx = self.e
        ty = self.f
        sx = math.hypot(self.a, self.b)
        sy = math.hypot(self.c, self.d)
        rot = math.atan2(self.b, self.a)
        return (tx, ty, sx, sy, rot)

    def inverse(self) -> "Transform2D":
        """计算逆变换。"""
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            return Transform2D()
        inv_a = self.d / det
        inv_b = -self.b / det
        inv_c = -self.c / det
        inv_d = self.a / det
        inv_e = -(self.a * self.e + self.c * self.f) / det
        inv_f = -(self.b * self.e + self.d * self.f) / det
        return Transform2D(a=inv_a, b=inv_b, c=inv_c,
                           d=inv_d, e=inv_e, f=inv_f)

    @classmethod
    def identity(cls) -> "Transform2D":
        return cls(a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0)

    @classmethod
    def from_translation(cls, tx: float, ty: float) -> "Transform2D":
        return cls(a=1.0, b=0.0, c=0.0, d=1.0, e=tx, f=ty)

    @classmethod
    def from_scale(cls, sx: float, sy: float) -> "Transform2D":
        return cls(a=sx, b=0.0, c=0.0, d=sy, e=0.0, f=0.0)


# ============================================================================
# 3. 匹配/压缩产物数据结构
# ============================================================================


@dataclass
class MatchedComponent:
    """一个子轮廓的匹配结果。"""

    component_id: int
    transform: Transform2D
    match_score: float  # 0..1，越大越相似
    bbox_iou: float     # 基于 bbox 的 IoU（粗筛指标）
    area_sim: float     # 面积相似度
    candidate_sub_index: int  # 对应原 glyph 子轮廓列表的下标


@dataclass
class CompressedGlyph:
    """压缩后的单个字形。"""

    unicode: int
    char: str
    mode: Literal["COMPONENT", "RAW"]
    components: list[MatchedComponent] = field(default_factory=list)
    raw_contours: list = field(default_factory=list)  # list[Contour]
    average_score: float = 0.0

    def estimate_size(self) -> int:
        """粗略估算存储成本（'点数当量'）。"""
        if self.mode == "COMPONENT":
            return len(self.components) * (1 + 6)
        return sum(len(c) for c in self.raw_contours)


@dataclass
class RouteAResult:
    """路线A的整批压缩结果。"""

    total_chars: int = 0
    component_mode_chars: int = 0
    raw_mode_chars: int = 0
    total_components: int = 0
    component_dictionary: dict[int, "SubContour"] = field(default_factory=dict)
    compressed_glyphs: list[CompressedGlyph] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"总字数: {self.total_chars}",
            f"  - 部件模式: {self.component_mode_chars}",
            f"  - RAW 模式: {self.raw_mode_chars}",
            f"部件库大小: {len(self.component_dictionary)}",
            f"部件引用总次数: {self.total_components}",
        ]
        for k, v in self.stats.items():
            if isinstance(v, float):
                lines.append(f"{k}: {v:.4f}")
            else:
                lines.append(f"{k}: {v}")
        return "\n".join(lines)


# ============================================================================
# 4. 位图栅格化与 IoU 计算（纯 Python，无外部依赖）
# ============================================================================


def _sub_bbox(sub: "SubContour") -> tuple[float, float, float, float]:
    """返回 (xmin, ymin, xmax, ymax)。空轮廓返回 (0,0,0,0)。"""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    found = False
    for c in sub.contours:
        for p in c.points:
            found = True
            if p.x < xmin:
                xmin = p.x
            if p.x > xmax:
                xmax = p.x
            if p.y < ymin:
                ymin = p.y
            if p.y > ymax:
                ymax = p.y
    if not found:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(xmin), float(ymin), float(xmax), float(ymax))


def _sub_area(sub: "SubContour") -> float:
    """粗略估算子轮廓的面积（用 bbox 面积作为近似）。"""
    xmin, ymin, xmax, ymax = _sub_bbox(sub)
    return (xmax - xmin) * (ymax - ymin)


def _point_in_polygon(px: float, py: float,
                      poly: list[tuple[float, float]]) -> bool:
    """射线法判断点是否在多边形内（包含边界）。"""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)):
            x_intersect = (xj - xi) * (py - yi) / (yj - yi + 1e-18) + xi
            if px < x_intersect:
                inside = not inside
        j = i
    return inside


def rasterize_contours_to_bitmap(
    sub: "SubContour",
    bbox: tuple[float, float, float, float],
    size: int,
) -> list[list[bool]]:
    """
    将子轮廓栅格化到 size x size 的位图。
    y=0 对应 bbox 的 ymin（底部）。
    """
    xmin, ymin, xmax, ymax = bbox
    w = max(1.0, xmax - xmin)
    h = max(1.0, ymax - ymin)
    bitmap: list[list[bool]] = [[False] * size for _ in range(size)]

    polygons: list[list[tuple[float, float]]] = []
    for c in sub.contours:
        if len(c.points) < 2:
            continue
        poly: list[tuple[float, float]] = []
        for p in c.points:
            nx = (float(p.x) - xmin) / w * size
            ny = (float(p.y) - ymin) / h * size
            poly.append((nx, ny))
        polygons.append(poly)

    for row in range(size):
        py_val = row + 0.5
        for col in range(size):
            px_val = col + 0.5
            count = 0
            for poly in polygons:
                if _point_in_polygon(px_val, py_val, poly):
                    count += 1
            if count % 2 == 1:
                bitmap[row][col] = True

    return bitmap


def _bitmap_union_size(a: list[list[bool]],
                       b: list[list[bool]]) -> tuple[int, int]:
    """返回 (intersection_count, union_count)。"""
    inter = 0
    union = 0
    size = len(a)
    for row in range(size):
        ra, rb = a[row], b[row]
        for col in range(size):
            va, vb = ra[col], rb[col]
            if va and vb:
                inter += 1
            if va or vb:
                union += 1
    return inter, union


def bitmap_iou(sub_a: "SubContour", sub_b: "SubContour",
               size: int = 128) -> float:
    """
    将两个子轮廓各自用自己的 bbox 归一化后栅格化，再计算 IoU。
    两个子轮廓都会被"平移到原点+缩放到 size x size 正方形"。
    返回 0..1 的 IoU 值。
    """
    if not sub_a.contours or not sub_b.contours:
        return 0.0
    bbox_a = _sub_bbox(sub_a)
    bbox_b = _sub_bbox(sub_b)
    if bbox_a[2] <= bbox_a[0] or bbox_b[2] <= bbox_b[0]:
        return 0.0
    bm_a = rasterize_contours_to_bitmap(sub_a, bbox_a, size)
    bm_b = rasterize_contours_to_bitmap(sub_b, bbox_b, size)
    inter, union = _bitmap_union_size(bm_a, bm_b)
    return inter / union if union > 0 else 0.0


# ============================================================================
# 5. 拟合仿射变换
# ============================================================================


def _bbox_iou_of(a: tuple[float, float, float, float],
                 b: tuple[float, float, float, float]) -> float:
    """两个 bbox 的 IoU。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1) * (by2 - by1))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def fit_transform(source: "SubContour", target: "SubContour",
                  config: RouteAConfig) -> Transform2D:
    """
    用 source 的 bbox 映射到 target 的 bbox，估计一个仿射变换。
    - 如果 allow_rotation=False（默认），仅用 (scale_x, scale_y, offset_x, offset_y)。
    - 如果 allow_rotation=True，则额外做小角度估计（不超过 max_rotation_deg）。
    """
    sx_min, sy_min, sx_max, sy_max = _sub_bbox(source)
    tx_min, ty_min, tx_max, ty_max = _sub_bbox(target)
    sw = max(1.0, sx_max - sx_min)
    sh = max(1.0, sy_max - sy_min)
    tw = max(1.0, tx_max - tx_min)
    th = max(1.0, ty_max - ty_min)
    sx = tw / sw if config.allow_scale_x else (tw + th) / (sw + sh) * 0.5
    sy = th / sh if config.allow_scale_y else (tw + th) / (sw + sh) * 0.5
    scx, scy = (sx_min + sx_max) / 2.0, (sy_min + sy_max) / 2.0
    tcx, tcy = (tx_min + tx_max) / 2.0, (ty_min + ty_max) / 2.0
    return Transform2D(
        a=sx, b=0.0, c=0.0, d=sy,
        e=tcx - sx * scx,
        f=tcy - sy * scy,
    )


def match_candidate(candidate_sub: "SubContour",
                    library: dict[int, "SubContour"],
                    config: RouteAConfig
                    ) -> Optional[MatchedComponent]:
    """
    在部件库中找与 candidate_sub 最匹配的项。
    返回最高分（超过 iou_threshold）的匹配结果，否则 None。
    """
    if not candidate_sub.contours:
        return None
    cand_bbox = _sub_bbox(candidate_sub)
    cand_size = max(cand_bbox[2] - cand_bbox[0],
                     cand_bbox[3] - cand_bbox[1])
    if cand_size < config.min_component_size:
        return None
    best: Optional[MatchedComponent] = None
    best_score = 0.0
    for comp_id, comp_sub in library.items():
        comp_bbox = _sub_bbox(comp_sub)
        comp_size = max(comp_bbox[2] - comp_bbox[0],
                        comp_bbox[3] - comp_bbox[1])
        if comp_size <= 0:
            continue
        ratio = cand_size / comp_size
        if ratio < 0.3 or ratio > 3.0:
            continue
        try:
            t = fit_transform(comp_sub, candidate_sub, config)
        except Exception:
            continue
        warped = t.apply_subcontour(comp_sub)
        iou = bitmap_iou(warped, candidate_sub, size=config.bitmap_size)
        if iou > best_score:
            best_score = iou
            if iou >= config.iou_threshold:
                bbox_iou = _bbox_iou_of(_sub_bbox(warped), cand_bbox)
                area_a = _sub_area(warped)
                area_b = _sub_area(candidate_sub)
                area_sim = (min(area_a, area_b) / max(area_a, area_b)
                            if max(area_a, area_b) > 0 else 0.0)
                best = MatchedComponent(
                    component_id=comp_id,
                    transform=t,
                    match_score=iou,
                    bbox_iou=bbox_iou,
                    area_sim=area_sim,
                    candidate_sub_index=-1,
                )
    return best


# ============================================================================
# 6. 路线A 编码器
# ============================================================================


class RouteAEncoder:
    """
    路线A：无损/有损的部件匹配压缩。

    流程:
      1. 对每个 glyph 调用 contour_decompose 切成子轮廓组
      2. 前 N 个字（seed_chars）的子轮廓作为初始部件库
      3. 之后每个字的每个子轮廓去部件库做 bitmap IoU 匹配
      4. 成功匹配：保存 component_id + transform
      5. 失败：添加到部件库作为新部件（或走 raw fallback）
    """

    def __init__(self, config: Optional[RouteAConfig] = None):
        self.config = config or RouteAConfig()

    def encode(self, font_glyphs: list,
               progress_callback: Any = None) -> RouteAResult:  # list[GlyphContours]
        """对一批字形做压缩。progress_callback(current, total) 可选。"""
        from .decomposer import SubContour, contour_decompose
        result = RouteAResult()
        start = time.time()
        if not font_glyphs:
            result.stats["error"] = "空输入"
            return result

        total = len(font_glyphs)
        glyph_subs: list[tuple] = []  # (glyph, subs)
        for i, g in enumerate(font_glyphs):
            subs = contour_decompose(g)
            glyph_subs.append((g, subs))
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(i + 1, total)

        library: dict[int, SubContour] = {}
        next_comp_id = 0

        def add_to_library(sub: SubContour) -> int:
            nonlocal next_comp_id
            cid = next_comp_id
            library[cid] = sub
            next_comp_id += 1
            return cid

        # 前 N 个字作为种子
        seed_count = min(self.config.seed_chars, len(glyph_subs))
        seed_comp_ids: list[list[int]] = []
        for idx in range(seed_count):
            _, subs = glyph_subs[idx]
            ids: list[int] = []
            for s in subs:
                bbox = _sub_bbox(s)
                sz = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
                if sz >= self.config.min_component_size:
                    ids.append(add_to_library(s))
                else:
                    ids.append(-1)
            seed_comp_ids.append(ids)

        total_score = 0.0
        score_count = 0
        match_attempts = 0
        match_successes = 0

        for idx, (glyph, subs) in enumerate(glyph_subs):
            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback(idx + 1, total)
            ch = (chr(glyph.unicode)
                  if 0 < glyph.unicode < 0x110000
                  else f"U{glyph.unicode:04X}")
            if glyph.is_empty() or not subs:
                result.raw_mode_chars += 1
                result.compressed_glyphs.append(CompressedGlyph(
                    unicode=glyph.unicode, char=ch, mode="RAW",
                    raw_contours=list(glyph.contours), average_score=0.0,
                ))
                continue

            components: list[MatchedComponent] = []
            raw_contours: list = []
            total_score_local = 0.0
            local_count = 0

            if idx < seed_count:
                for si, s in enumerate(subs):
                    cid = seed_comp_ids[idx][si]
                    if cid >= 0:
                        t = fit_transform(library[cid], s, self.config)
                        score = bitmap_iou(library[cid], s,
                                           size=self.config.bitmap_size)
                        components.append(MatchedComponent(
                            component_id=cid, transform=t,
                            match_score=score, bbox_iou=1.0,
                            area_sim=1.0, candidate_sub_index=si,
                        ))
                        total_score_local += score
                        local_count += 1
                    else:
                        raw_contours.extend(s.contours)
            else:
                for si, s in enumerate(subs):
                    match_attempts += 1
                    m = match_candidate(s, library, self.config)
                    if m is not None:
                        m.candidate_sub_index = si
                        components.append(m)
                        total_score_local += m.match_score
                        local_count += 1
                        match_successes += 1
                    else:
                        bbox = _sub_bbox(s)
                        sz = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
                        if sz >= self.config.min_component_size:
                            cid = add_to_library(s)
                            t = fit_transform(library[cid], s, self.config)
                            components.append(MatchedComponent(
                                component_id=cid, transform=t,
                                match_score=1.0, bbox_iou=1.0,
                                area_sim=1.0, candidate_sub_index=si,
                            ))
                            total_score_local += 1.0
                            local_count += 1
                        else:
                            raw_contours.extend(s.contours)

            avg = (total_score_local / local_count) if local_count > 0 else 0.0
            matched_any = len(components) > 0
            if matched_any and not raw_contours:
                result.component_mode_chars += 1
                mode: Literal["COMPONENT", "RAW"] = "COMPONENT"
                result.total_components += len(components)
            elif not matched_any and raw_contours:
                result.raw_mode_chars += 1
                mode = "RAW"
            else:
                result.component_mode_chars += 1
                mode = "COMPONENT"
                result.total_components += len(components)

            result.compressed_glyphs.append(CompressedGlyph(
                unicode=glyph.unicode, char=ch, mode=mode,
                components=components,
                raw_contours=raw_contours,
                average_score=avg,
            ))
            total_score += total_score_local
            score_count += local_count

        if progress_callback:
            progress_callback(total, total)

        result.total_chars = len(glyph_subs)
        result.component_dictionary = library
        elapsed = time.time() - start
        raw_total = sum(sum(len(c) for c in g.contours) for g, _ in glyph_subs)
        comp_total = sum(cg.estimate_size() for cg in result.compressed_glyphs)
        ratio = (1.0 - comp_total / raw_total) if raw_total > 0 else 0.0

        result.stats = {
            "elapsed_sec": elapsed,
            "avg_match_score": (total_score / score_count) if score_count > 0 else 0.0,
            "match_attempts": match_attempts,
            "match_successes": match_successes,
            "match_success_rate": (match_successes / match_attempts) if match_attempts > 0 else 0.0,
            "library_size": len(library),
            "raw_point_total": raw_total,
            "compressed_point_total": comp_total,
            "estimated_compression_ratio": ratio,
        }
        return result

    def decode(self, result: RouteAResult) -> dict[int, list]:
        """解码重建每个字的轮廓列表。返回 {unicode: [Contour, ...]}。"""
        output: dict[int, list] = {}
        for cg in result.compressed_glyphs:
            contours: list = []
            if cg.mode == "COMPONENT":
                for m in cg.components:
                    comp_sub = result.component_dictionary.get(m.component_id)
                    if comp_sub is None:
                        continue
                    warped = m.transform.apply_subcontour(comp_sub)
                    contours.extend(warped.contours)
                contours.extend(cg.raw_contours)
            else:
                contours.extend(cg.raw_contours)
            output[cg.unicode] = contours
        return output
