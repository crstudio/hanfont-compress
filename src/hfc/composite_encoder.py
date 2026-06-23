"""
模块4: 路线C——基于 TrueType Composite Glyph 的标准字体内复用。

核心思路:
  1. 对每个汉字 glyph 做连通域拆分（contour_decompose），得到子轮廓组。
  2. 用归一化 + Hausdorff 距离做几何聚类，找出"跨字重复出现"的共享部件。
  3. 每个共享部件 → 在 glyf 表中创建一个独立的简单 glyph（命名 _part_NNN）。
  4. 每个原始汉字 glyph → 重写为 composite glyph，引用部件 glyphs，
     并用 offset + scale 来定位/缩放部件。
  5. 若某个子轮廓的几何变换超过简单 scale + translate 能表达的范围，
     则该汉字保留为原始简单 glyph（RAW fallback）。

输出:
  - 一个标准的 .ttf 文件（可被任何字体渲染引擎直接使用）
  - 若 fontTools 支持，还可输出 .woff2

参考: docs/try.md 第 6.16-6.22 节（路线 C）
     TrueType Reference Manual - 'glyf' table (Composite Glyph)
"""

from __future__ import annotations

import io
import math
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

# 兼容两种运行方式：作为模块导入（包内）或直接执行（__main__）
if __package__:
    from .decomposer import (
        SubContour,
        components_match,
        contour_decompose,
        normalize,
    )
    from .glyph_extractor import Contour, ContourPoint, GlyphContours
else:
    import sys as _sys
    from pathlib import Path as _Path

    _src_root = _Path(__file__).resolve().parent.parent
    if str(_src_root) not in _sys.path:
        _sys.path.insert(0, str(_src_root))
    from hfc.decomposer import (
        SubContour,
        components_match,
        contour_decompose,
        normalize,
    )
    from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours


# ============================================================================
# 1. 配置
# ============================================================================


@dataclass
class RouteCConfig:
    """路线C（Composite Glyph 字体内复用）配置。"""

    reuse_threshold: float = 0.90  # 几何相似度阈值
    min_component_size: int = 300  # 字体单位。小于该尺寸的子轮廓直接并入 RAW
    allow_scale_x: bool = True  # 是否允许 X 方向独立缩放
    allow_scale_y: bool = True  # 是否允许 Y 方向独立缩放
    output_ttf: bool = True

    @property
    def hausdorff_tolerance(self) -> float:
        return max(0.0, (1.0 - self.reuse_threshold) * math.sqrt(2.0))


# ============================================================================
# 2. 结果数据结构
# ============================================================================


@dataclass
class ComponentInfo:
    """一个共享部件的元信息。"""

    component_id: str
    glyph_name: str
    appearance_count: int
    representative: SubContour
    original_bbox: tuple[float, float, float, float]


@dataclass
class RouteCResult:
    """路线C的完整结果。"""

    original_font_size: int
    new_ttf_size: int
    component_glyphs_created: int
    chars_recomposed: int
    chars_raw_fallback: int
    component_list: list[ComponentInfo] = field(default_factory=list)
    glyph_map: dict[int, list[str]] = field(default_factory=dict)
    output_path: Optional[str] = None


# ============================================================================
# 3. 变换: 从共享部件坐标到原字形坐标的 scale+translate
# ============================================================================


def _bbox(sub: SubContour) -> tuple[float, float, float, float]:
    x_min = y_min = float("inf")
    x_max = y_max = float("-inf")
    for c in sub.contours:
        for p in c.points:
            if p.x < x_min:
                x_min = p.x
            if p.x > x_max:
                x_max = p.x
            if p.y < y_min:
                y_min = p.y
            if p.y > y_max:
                y_max = p.y
    return (x_min, y_min, x_max, y_max)


@dataclass
class ScaleTranslate:
    """TTF composite 能用的简化变换: (sx, sy, dx, dy)。"""

    sx: float
    sy: float
    dx: float  # x 偏移（字体单位）
    dy: float  # y 偏移（字体单位）

    def as_affine_tuple(self) -> tuple[float, float, float, float, float, float]:
        """返回 (a, b, c, d, e, f) 6 参数仿射矩阵。"""
        # x' = sx*x + dx, y' = sy*y + dy → (sx, 0, 0, sy, dx, dy)
        return (self.sx, 0.0, 0.0, self.sy, self.dx, self.dy)


def fit_scale_translate(source: SubContour, target: SubContour) -> ScaleTranslate:
    """用 bbox 估计 scale + translate，把 source 映射到 target 的位置。"""
    sx0, sy0, sx1, sy1 = _bbox(source)
    tx0, ty0, tx1, ty1 = _bbox(target)
    sw = max(1.0, sx1 - sx0)
    sh = max(1.0, sy1 - sy0)
    tw = max(1.0, tx1 - tx0)
    th = max(1.0, ty1 - ty0)
    sx = tw / sw
    sy = th / sh
    # 把 source 的左下角对齐到 target 的左下角。
    dx = tx0 - sx * sx0
    dy = ty0 - sy * sy0
    return ScaleTranslate(sx, sy, dx, dy)


# ============================================================================
# 4. 几何聚类: 发现跨字共享部件
# ============================================================================


@dataclass
class _ClusterMember:
    char_idx: int        # 第几个字
    sub_idx: int         # 该字的第几个子轮廓
    sub: SubContour      # 原始子轮廓数据
    bbox: tuple[float, float, float, float]


def find_shared_components(
    subs_with_owner: list[tuple[int, int, SubContour]],
    tol: float,
) -> list[list[_ClusterMember]]:
    """
    对子轮廓做并查集聚类。
    subs_with_owner: [(char_idx, sub_idx, sub), ...]
    返回: 聚类后的组列表，每组是若干 _ClusterMember（几何一致）
    """
    members: list[_ClusterMember] = []
    for ci, si, sub in subs_with_owner:
        members.append(_ClusterMember(ci, si, sub, _bbox(sub)))

    n = len(members)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 做尺寸粗筛 + Hausdorff 匹配
    for i in range(n):
        a_bbox = members[i].bbox
        a_size = max(a_bbox[2] - a_bbox[0], a_bbox[3] - a_bbox[1])
        for j in range(i + 1, n):
            b_bbox = members[j].bbox
            b_size = max(b_bbox[2] - b_bbox[0], b_bbox[3] - b_bbox[1])
            ratio = a_size / b_size if b_size > 0 else 1.0
            if ratio < 0.2 or ratio > 5.0:
                continue
            res = components_match(members[i].sub, members[j].sub, tolerance=tol)
            if res.match:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)

    result: list[list[_ClusterMember]] = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue  # 只出现过一次的不构成"共享"
        result.append([members[i] for i in idxs])

    # 按出现次数从多到少排序
    result.sort(key=lambda g: -len(g))
    return result


# ============================================================================
# 5. RouteCEncoder 主类
# ============================================================================


class RouteCEncoder:
    """
    路线C编码器：把汉字 glyphs 重写为 composite glyph，引用共享部件。
    """

    def __init__(self, config: Optional[RouteCConfig] = None) -> None:
        self.config = config or RouteCConfig()

    # ---- 对外主接口 ----

    def encode(
        self,
        font_path: str | Path,
        glyphs: list[GlyphContours],
        output_path: Optional[str | Path] = None,
    ) -> RouteCResult:
        """
        执行路线C编码:
        1) 连通域拆分
        2) 几何聚类 → 共享部件
        3) 新建部件 glyphs
        4) 重写汉字 glyphs 为 composite
        5) 写出新 .ttf

        Parameters
        ----------
        font_path : 原始字体文件路径（用于拷贝 head/name/maxp/hhea 等基础表）
        glyphs    : 要处理的汉字 glyph 列表（从 GlyphExtractor 得到）
        output_path: 可选，新 TTF 输出路径。不提供时写到临时文件。

        返回 RouteCResult。
        """
        t0 = time.time()
        original_size = int(Path(font_path).stat().st_size)

        # ---- (1) 对每个 glyph 做连通域拆分 ----
        char_subs: list[list[SubContour]] = []  # 每个字的子轮廓列表
        subs_with_owner: list[tuple[int, int, SubContour]] = []
        for ci, g in enumerate(glyphs):
            subs = contour_decompose(g)
            # 过滤掉太小的子轮廓，它们不适合作为"共享部件"提取
            filtered: list[SubContour] = []
            for s in subs:
                bb = _bbox(s)
                sz = max(bb[2] - bb[0], bb[3] - bb[1])
                if sz >= self.config.min_component_size:
                    filtered.append(s)
            char_subs.append(filtered)
            for si, s in enumerate(filtered):
                subs_with_owner.append((ci, si, s))

        # ---- (2) 发现共享部件 ----
        clusters = find_shared_components(
            subs_with_owner, self.config.hausdorff_tolerance
        )

        # 把每个子轮廓映射到它所属的共享部件（如果有的话）
        # key: (char_idx, sub_idx) → component_name
        sub_to_component: dict[tuple[int, int], str] = {}
        component_list: list[ComponentInfo] = []

        # 为了判定"哪些汉字已被完全覆盖"，记录每个子轮廓是否匹配到某个部件
        # 同时选择代表轮廓（最大尺寸的那个）
        for ci, cluster in enumerate(clusters):
            name = f"_part_{ci:03d}"
            # 选尺寸最大的做代表
            cluster_sorted = sorted(
                cluster,
                key=lambda m: -(
                    (m.bbox[2] - m.bbox[0]) * (m.bbox[3] - m.bbox[1])
                ),
            )
            rep = cluster_sorted[0]
            component_list.append(
                ComponentInfo(
                    component_id=name,
                    glyph_name=name,
                    appearance_count=len(cluster),
                    representative=rep.sub,
                    original_bbox=rep.bbox,
                )
            )
            for m in cluster:
                sub_to_component[(m.char_idx, m.sub_idx)] = name

        # ---- (3) 构造新 TTF ----
        new_font = self._clone_base_font(font_path)
        glyf = new_font["glyf"]
        hmtx = new_font["hmtx"]
        advance_width, _lsb = hmtx[list(hmtx.keys())[0]]

        # 写入部件 glyphs
        component_glyph_set: set[str] = set()
        for info in component_list:
            glyph = self._subcontour_to_glyph(info.representative)
            glyf.glyphs[info.glyph_name] = glyph
            component_glyph_set.add(info.glyph_name)
            # 部件字形的 advance width 用自身尺寸
            bb = info.original_bbox
            w = int(bb[2] - bb[0]) if bb[2] > bb[0] else 500
            hmtx[info.glyph_name] = (max(200, w), 0)

        # 为每个汉字构造 composite glyph（若全部子轮廓都能匹配到共享部件）
        recomposed = 0
        raw_fallback = 0
        glyph_map: dict[int, list[str]] = {}

        for ci, g in enumerate(glyphs):
            subs = char_subs[ci]
            # 要求：该字的所有子轮廓都被某个共享部件覆盖
            matched_parts: list[tuple[str, ScaleTranslate, int]] = []
            all_matched = True
            for si, s in enumerate(subs):
                cname = sub_to_component.get((ci, si))
                if cname is None:
                    all_matched = False
                    break
                # 找到对应的代表部件，估计 scale+translate
                rep = next(
                    (c for c in component_list if c.glyph_name == cname),
                    None,
                )
                if rep is None:
                    all_matched = False
                    break
                st = fit_scale_translate(rep.representative, s)
                matched_parts.append((cname, st, si))

            unicode_val = g.unicode
            glyph_name = self._unicode_glyph_name(unicode_val)

            if all_matched and subs:
                # 构造 composite glyph
                pen = TTGlyphPen(component_glyph_set)
                for cname, st, _ in matched_parts:
                    pen.addComponent(cname, st.as_affine_tuple())
                comp_glyph = pen.glyph()
                # 拷贝原 advance width
                aw = advance_width
                # 若原字体中已有这个字形名，用它的 advance width
                if glyph_name in hmtx:
                    aw = hmtx[glyph_name][0]
                glyf.glyphs[glyph_name] = comp_glyph
                hmtx[glyph_name] = (aw, 0)
                recomposed += 1
                glyph_map[unicode_val] = [p[0] for p in matched_parts]
            else:
                # RAW fallback：保留原始轮廓
                raw_glyph = self._glyphcontours_to_glyph(g)
                if raw_glyph is not None:
                    glyf.glyphs[glyph_name] = raw_glyph
                    aw = advance_width
                    if glyph_name in hmtx:
                        aw = hmtx[glyph_name][0]
                    hmtx[glyph_name] = (aw, 0)
                raw_fallback += 1
                glyph_map[unicode_val] = []

        # 构建/更新 cmap（保留原字体的 cmap 表，并为我们处理过的字做映射）
        self._ensure_cmap(new_font, glyphs)

        # ---- (5) 保存 ----
        if output_path is None:
            fd, tmp_path = tempfile.mkstemp(suffix=".ttf")
            import os

            os.close(fd)
            out_path = Path(tmp_path)
        else:
            out_path = Path(output_path)

        new_font.save(str(out_path))
        new_size = int(out_path.stat().st_size)
        elapsed = time.time() - t0

        return RouteCResult(
            original_font_size=original_size,
            new_ttf_size=new_size,
            component_glyphs_created=len(component_list),
            chars_recomposed=recomposed,
            chars_raw_fallback=raw_fallback,
            component_list=component_list,
            glyph_map=glyph_map,
            output_path=str(out_path),
        )

    # ---- 内部工具 ----

    def _clone_base_font(self, font_path: str | Path) -> TTFont:
        """拷贝原字体的基础表，清空 glyf 中除 .notdef 外的字形。"""
        font = TTFont(str(font_path))

        # 清空 glyf 表中已有的字形（保留 .notdef）
        if "glyf" in font:
            glyf = font["glyf"]
            keep = {".notdef"}
            new_glyphs = {k: v for k, v in glyf.glyphs.items() if k in keep}
            glyf.glyphs = new_glyphs

        # 同步清理 hmtx 中被删字形的度量信息
        if "hmtx" in font:
            hmtx = font["hmtx"]
            new_metrics = {}
            for gname in font.getGlyphOrder():
                if gname in hmtx.metrics:
                    new_metrics[gname] = hmtx.metrics[gname]
            hmtx.metrics = new_metrics

        return font

    def _subcontour_to_glyph(self, sub: SubContour):
        """用 TTGlyphPen 把一个子轮廓转成 glyf 表中的 simple glyph。"""
        pen = TTGlyphPen(None)
        for contour in sub.contours:
            if not contour.points:
                continue
            pts = [(int(p.x), int(p.y)) for p in contour.points]
            pen.moveTo(pts[0])
            for p in pts[1:]:
                pen.lineTo(p)
            pen.closePath()
        g = pen.glyph()
        return g

    def _glyphcontours_to_glyph(self, g: GlyphContours):
        """把原始 GlyphContours 整个转成 simple glyf glyph（RAW fallback）。"""
        if not g.contours:
            return None
        pen = TTGlyphPen(None)
        for contour in g.contours:
            if not contour.points:
                continue
            pts = [(int(p.x), int(p.y)) for p in contour.points]
            pen.moveTo(pts[0])
            for p in pts[1:]:
                pen.lineTo(p)
            pen.closePath()
        return pen.glyph()

    def _unicode_glyph_name(self, uv: int) -> str:
        """用标准的 uniXXXX / uXXXXXX 命名。"""
        if uv <= 0xFFFF:
            return f"uni{uv:04X}"
        return f"u{uv:06X}"

    def _ensure_cmap(self, font: TTFont, glyphs: list[GlyphContours]) -> None:
        """确保新字体的 cmap 表中包含我们处理过的字符 → glyph 名的映射。"""
        # 找一个 4 字节格式的 cmap subtable（Unicode BMP）
        cmap_table = font.get("cmap")
        if cmap_table is None:
            cmap_table = newTable("cmap")
            font["cmap"] = cmap_table
            from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

            sub = CmapSubtable.newSubtable(4)
            sub.platformID = 3
            sub.platEncID = 1
            sub.language = 0
            sub.cmap = {}
            cmap_table.tableVersion = 0
            cmap_table.tables = [sub]

        # 取最佳 subtable（优先 platform 3 enc 1, format 4）
        target = None
        for sub in cmap_table.tables:
            if sub.platformID == 3 and sub.platEncID == 1:
                target = sub
                break
        if target is None:
            target = cmap_table.tables[0]

        for g in glyphs:
            if g.unicode and g.unicode > 0:
                target.cmap[g.unicode] = self._unicode_glyph_name(g.unicode)

        # 更新 glyphOrder（否则 fontTools 可能不认新 glyph）
        existing_order = list(font.getGlyphOrder())
        existing_set = set(existing_order)
        new_names = []
        for g in glyphs:
            gn = self._unicode_glyph_name(g.unicode)
            if gn not in existing_set:
                new_names.append(gn)
                existing_set.add(gn)
        for cname in [c.glyphName for cname_key in list(target.cmap.values()) if False]:
            pass
        font.setGlyphOrder(existing_order + new_names)


# ============================================================================
# 6. Demo
# ============================================================================


if __name__ == "__main__":
    # 简易 demo: 合成几个"有共享方块部件"的字形，走一遍编码流程。
    def _make_box_glyph(x0: int, y0: int, x1: int, y1: int, uv: int) -> GlyphContours:
        g = GlyphContours(unicode=uv)
        c = g.add_contour()
        c.add_point(x0, y0, True)
        c.add_point(x1, y0, True)
        c.add_point(x1, y1, True)
        c.add_point(x0, y1, True)
        return g

    def _make_two_box_glyph(x0a, y0a, x1a, y1a, x0b, y0b, x1b, y1b, uv) -> GlyphContours:
        g = GlyphContours(unicode=uv)
        c1 = g.add_contour()
        c1.add_point(x0a, y0a, True)
        c1.add_point(x1a, y0a, True)
        c1.add_point(x1a, y1a, True)
        c1.add_point(x0a, y1a, True)
        c2 = g.add_contour()
        c2.add_point(x0b, y0b, True)
        c2.add_point(x1b, y0b, True)
        c2.add_point(x1b, y1b, True)
        c2.add_point(x0b, y1b, True)
        return g

    # 构造 5 个字: 3 个是 400x400 方块, 2 个是"两个方块组合"
    test_glyphs: list[GlyphContours] = [
        _make_box_glyph(0, 0, 400, 400, 0x4E00),  # 一
        _make_box_glyph(0, 0, 400, 400, 0x4E8C),  # 二（形状同"一"）
        _make_box_glyph(0, 0, 400, 400, 0x4E09),  # 三（形状同"一"）
        _make_two_box_glyph(0, 0, 400, 400, 420, 0, 820, 400, 0x660E),  # 左右两个方块
        _make_two_box_glyph(0, 0, 400, 400, 0, 420, 400, 820, 0x6708),  # 上下两个方块
    ]

    # 需要一个真实 ttf 做"基础表"。这里用 temp file 生成一个最小的字体。
    from fontTools.ttLib import TTFont as _TTF
    from fontTools.pens.ttGlyphPen import TTGlyphPen as _TPen

    base_font = _TTF()
    base_font.setGlyphOrder([".notdef"])
    base_font["head"] = newTable("head")
    h = base_font["head"]
    h.tableVersion = 1.0
    h.fontRevision = 1.0
    h.checkSumAdjustment = 0
    h.magicNumber = 0x5F0F3CF5
    h.flags = 0
    h.unitsPerEm = 1000
    h.created = h.modified = 3600000000
    h.magicNumber = 0x5F0F3CF5
    h.xMin = 0
    h.yMin = 0
    h.xMax = 1000
    h.yMax = 1000
    h.macStyle = 0
    h.lowestRecPPEM = 6
    h.fontDirectionHint = 2
    h.indexToLocFormat = 0
    h.glyphDataFormat = 0

    base_font["hhea"] = newTable("hhea")
    hh = base_font["hhea"]
    hh.tableVersion = 0x00010000
    hh.ascent = 800
    hh.descent = -200
    hh.lineGap = 0
    hh.advanceWidthMax = 1000
    hh.minLeftSideBearing = 0
    hh.minRightSideBearing = 0
    hh.xMaxExtent = 1000
    hh.caretSlopeRise = 1
    hh.caretSlopeRun = 0
    hh.caretOffset = 0
    hh.reserved0 = hh.reserved1 = hh.reserved2 = hh.reserved3 = 0
    hh.metricDataFormat = 0
    hh.numberOfHMetrics = 1

    base_font["maxp"] = newTable("maxp")
    mp = base_font["maxp"]
    mp.tableVersion = 0x00010000
    mp.numGlyphs = 1
    mp.maxPoints = 0
    mp.maxContours = 0
    mp.maxCompositePoints = 0
    mp.maxCompositeContours = 0
    mp.maxZones = 1
    mp.maxTwilightPoints = 0
    mp.maxStorage = 0
    mp.maxFunctionDefs = 0
    mp.maxInstructionDefs = 0
    mp.maxStackElements = 0
    mp.maxSizeOfInstructions = 0
    mp.maxComponentElements = 0
    mp.maxComponentDepth = 0

    base_font["glyf"] = newTable("glyf")
    base_font["glyf"].glyphs = {}
    np = _TPen(None)
    base_font["glyf"].glyphs[".notdef"] = np.glyph()

    base_font["loca"] = newTable("loca")

    base_font["hmtx"] = newTable("hmtx")
    base_font["hmtx"].metrics = {".notdef": (500, 0)}

    base_font["name"] = newTable("name")
    base_font["name"].names = []

    base_font["cmap"] = newTable("cmap")
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    sub = CmapSubtable.newSubtable(4)
    sub.platformID = 3
    sub.platEncID = 1
    sub.language = 0
    sub.cmap = {}
    base_font["cmap"].tableVersion = 0
    base_font["cmap"].tables = [sub]

    base_font["OS/2"] = newTable("OS/2")
    o2 = base_font["OS/2"]
    o2.version = 4
    o2.xAvgCharWidth = 500
    o2.usWeightClass = 400
    o2.usWidthClass = 5
    o2.fsType = 0
    o2.ySubscriptXSize = 650
    o2.ySubscriptYSize = 600
    o2.ySubscriptXOffset = 0
    o2.ySubscriptYOffset = 75
    o2.ySuperscriptXSize = 650
    o2.ySuperscriptYSize = 600
    o2.ySuperscriptXOffset = 0
    o2.ySuperscriptYOffset = 350
    o2.yStrikeoutSize = 50
    o2.yStrikeoutPosition = 300
    o2.sFamilyClass = 0
    o2.panose = bytes([0] * 10)
    o2.ulUnicodeRange1 = 0
    o2.ulUnicodeRange2 = 0
    o2.ulUnicodeRange3 = 0
    o2.ulUnicodeRange4 = 0
    o2.achVendID = "    "
    o2.fsSelection = 64
    o2.usFirstCharIndex = 0
    o2.usLastCharIndex = 0xFFFF
    o2.sTypoAscender = 800
    o2.sTypoDescender = -200
    o2.sTypoLineGap = 0
    o2.usWinAscent = 800
    o2.usWinDescent = 200
    o2.ulCodePageRange1 = 0
    o2.ulCodePageRange2 = 0

    base_font["post"] = newTable("post")
    base_font["post"].formatType = 3.0
    base_font["post"].extraNames = []
    base_font["post"].mapping = {}
    base_font["post"].glyphOrder = base_font.getGlyphOrder()

    import os as _os
    tmpfd, tmppath = tempfile.mkstemp(suffix=".ttf")
    _os.close(tmpfd)
    try:
        base_font.save(tmppath)
        encoder = RouteCEncoder(RouteCConfig(reuse_threshold=0.90, min_component_size=50))
        result = encoder.encode(tmppath, test_glyphs)

        print("=== Route C Demo ===")
        print(f"原字体大小:     {result.original_font_size} bytes")
        print(f"新字体大小:     {result.new_ttf_size} bytes")
        print(f"共享部件数:     {result.component_glyphs_created}")
        print(f"composite 字:   {result.chars_recomposed}")
        print(f"raw fallback:   {result.chars_raw_fallback}")
        print(f"输出路径:       {result.output_path}")
        for info in result.component_list:
            print(f"  - {info.component_id}: 出现 {info.appearance_count} 次")
    except Exception as e:
        print(f"Route C demo 需要真实字体文件（创建合成 TTF 失败: {e}）")
    finally:
        try:
            _os.unlink(tmppath)
        except Exception:
            pass


# 向后兼容别名（保留旧名）
CompositeEncoder = RouteCEncoder
