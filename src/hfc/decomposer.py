"""
汉字字形分解与部件匹配核心模块。

设计思路:
  1. BUSHOU_RADICALS: 公认偏旁部首表 + 常用字拆字规则。
     格式: { 字: [子部件1, 子部件2, ...] }
     递归: 若子部件本身又可拆，则继续拆。
  2. contour_decompose: 按连通域自动拆分字形。
     一个 glyph 的多个 contour 之间若不相交，就是可独立拆分的。
  3. component_match: 归一化(平移到原点 + 缩放到单位大小 + 翻转一致)
     后，做"全点精确重合"测试。能精确重合 -> 共用部件。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .glyph_extractor import Contour, ContourPoint, GlyphContours

# ============================================================================
# 1. 偏旁部首表
# ============================================================================

# 基本偏旁部首列表（可继续补充）
# 这些本身就是"部件"，不再继续拆
RADICAL_ROOT: set[str] = {
    # 1-2 画
    "一", "丨", "丿", "丶", "乙", "亅",
    "二", "十", "丁", "厂", "匚", "卜", "冂", "冖",
    "亠", "亻", "人", "入", "八", "丷", "儿",
    "匕", "几", "勹", "刀", "刂", "力", "又", "厶",
    "廴", "卩", "阝", "凵", "⺊", "⺋", "⺌",
    # 3 画
    "口", "囗", "土", "士", "夂", "夊", "夕", "大",
    "女", "子", "宀", "寸", "小", "尢", "尸", "山",
    "屮", "川", "工", "已", "己", "巳", "巾", "干",
    "广", "廾", "弋", "弓", "彐", "彡", "彳", "纟",
    "艹", "扌", "⺖", "⺗", "⺘", "⺙", "⺮", "⺼",
    # 4 画
    "心", "忄", "戈", "户", "手", "扌", "支", "攴",
    "文", "斗", "斤", "方", "日", "曰", "月", "木",
    "欠", "止", "歹", "殳", "毋", "比", "毛", "氏",
    "气", "水", "氵", "火", "灬", "爪", "父", "爻",
    "片", "牛", "牜", "犬", "犭", "王", "玉", "瓦",
    "甘", "生", "用", "田", "疋", "疒", "癶", "白",
    "皮", "皿", "目", "矛", "矢", "石", "示", "礻",
    "禸", "禾", "穴", "立",
    # 5-6 画及以上常见偏旁
    "皮", "皿", "目", "矛", "矢", "石", "示", "礻",
    "禸", "禾", "穴", "立", "竹", "⺮", "米", "糸",
    "纟", "缶", "羊", "⺶", "羽", "老", "耂", "而",
    "耒", "耳", "聿", "肉", "月", "臣", "自", "至",
    "臼", "舌", "舛", "舟", "艮", "色", "虍", "虫",
    "血", "行", "衣", "衤", "覀", "见", "角", "言",
    "讠", "谷", "豆", "豕", "豸", "贝", "赤", "走",
    "足", "⻊", "身", "车", "辛", "辰", "辶", "邑",
    "酉", "釆", "里", "麦", "金", "钅", "长", "门",
    "阜", "隹", "雨", "青", "非", "齿", "鹿", "麻",
    "黄", "黑", "黹", "鼓", "鼠", "鼻", "齐",
    # 常见独体字（也是部件根）
    "上", "下", "中", "左", "右", "正", "天", "夫",
    "元", "无", "不", "为", "之", "久", "乎", "也",
    "乞", "于", "亏", "三", "四", "五", "六", "七",
    "八", "九", "百", "千", "万", "亿",
}

# 常用字的拆字规则 (持续补充)。值是按视觉结构拆分的部件。
# 注意: 这只是"视觉结构"的参考，实际能否共用部件由几何检测决定。
DECOMPOSE_RULES: dict[str, list[str]] = {
    # 左右结构
    "明": ["日", "月"],
    "好": ["女", "子"],
    "休": ["亻", "木"],
    "体": ["亻", "本"],
    "何": ["亻", "可"],
    "他": ["亻", "也"],
    "你": ["亻", "尔"],
    "们": ["亻", "门"],
    "从": ["人", "人"],
    "双": ["又", "又"],
    "林": ["木", "木"],
    "森": ["木", "木", "木"],
    "信": ["亻", "言"],
    "保": ["亻", "呆"],
    "打": ["扌", "丁"],
    "把": ["扌", "巴"],
    "找": ["扌", "戈"],
    "我": ["丿", "找"],   # 粗略
    "时": ["日", "寸"],
    "旺": ["日", "王"],
    "明": ["日", "月"],
    "晴": ["日", "青"],
    "胡": ["古", "月"],
    "期": ["其", "月"],
    "妈": ["女", "马"],
    "姐": ["女", "且"],
    "妹": ["女", "未"],
    "姑": ["女", "古"],
    "读": ["讠", "卖"],
    "说": ["讠", "兑"],
    "话": ["讠", "舌"],
    "请": ["讠", "青"],
    "认": ["讠", "人"],
    "识": ["讠", "只"],
    "语": ["讠", "吾"],
    "计": ["讠", "十"],
    "记": ["讠", "己"],
    "订": ["讠", "丁"],
    "河": ["氵", "可"],
    "湖": ["氵", "胡"],
    "海": ["氵", "每"],
    "江": ["氵", "工"],
    "汉": ["氵", "又"],
    "汗": ["氵", "干"],
    "洋": ["氵", "羊"],
    "洗": ["氵", "先"],
    "活": ["氵", "舌"],
    "注": ["氵", "主"],
    "泪": ["氵", "目"],
    "清": ["氵", "青"],
    "红": ["纟", "工"],
    "给": ["纟", "合"],
    "纸": ["纟", "氏"],
    "级": ["纟", "及"],
    "村": ["木", "寸"],
    "林": ["木", "木"],
    "柏": ["木", "白"],
    "松": ["木", "公"],
    "杨": ["木", "昜"],
    "根": ["木", "艮"],
    "树": ["木", "对", "寸"],
    "相": ["木", "目"],
    "查": ["木", "旦"],
    "机": ["木", "几"],
    "桥": ["木", "乔"],
    "校": ["木", "交"],
    "样": ["木", "羊"],
    "板": ["木", "反"],
    "杯": ["木", "不"],
    "杆": ["木", "干"],
    "杜": ["木", "土"],
    "材": ["木", "才"],
    "打": ["扌", "丁"],
    "指": ["扌", "旨"],
    "把": ["扌", "巴"],
    "报": ["扌", "扌"],
    "的": ["白", "勺"],
    "和": ["禾", "口"],
    "种": ["禾", "中"],
    "秋": ["禾", "火"],
    "科": ["禾", "斗"],
    "秒": ["禾", "少"],
    "称": ["禾", "尔"],
    "租": ["禾", "且"],
    "积": ["禾", "只"],
    "程": ["禾", "呈"],
    "饭": ["饣", "反"],
    "饿": ["饣", "我"],
    "饱": ["饣", "包"],
    "饲": ["饣", "司"],
    "馆": ["饣", "官"],
    "脸": ["月", "佥"],
    "肥": ["月", "巴"],
    "朋": ["月", "月"],
    "肚": ["月", "土"],
    "肝": ["月", "干"],
    "脚": ["月", "却"],
    "服": ["月", "菔"],
    "吃": ["口", "乞"],
    "叫": ["口", "丩"],
    "听": ["口", "斤"],
    "唱": ["口", "昌"],
    "吗": ["口", "马"],
    "呢": ["口", "尼"],
    "吧": ["口", "巴"],
    "味": ["口", "未"],
    "响": ["口", "向"],
    "哈": ["口", "合"],
    "咬": ["口", "交"],
    "喝": ["口", "曷"],
    "嘴": ["口", "觜"],
    "叶": ["口", "十"],
    "号": ["口", "丂"],
    "吐": ["口", "土"],
    "吹": ["口", "欠"],
    "吼": ["口", "孔"],
    "告": ["牛", "口"],
    "先": ["牛", "儿"],
    "告": ["牛", "口"],
    "加": ["力", "口"],
    "动": ["云", "力"],
    "功": ["工", "力"],
    "助": ["且", "力"],
    "努": ["奴", "力"],
    "灯": ["火", "丁"],
    "炒": ["火", "少"],
    "炎": ["火", "火"],
    "烧": ["火", "尧"],
    "烟": ["火", "因"],
    "灰": ["厂", "火"],
    "灵": ["彐", "火"],
    "点": ["占", "灬"],
    "热": ["埶", "灬"],
    "熊": ["能", "灬"],
    "然": ["肰", "灬"],
    "煮": ["者", "灬"],
    "照": ["昭", "灬"],
    "熟": ["孰", "灬"],
    "煎": ["前", "灬"],
    "爬": ["爪", "巴"],
    "父": ["八", "乂"],
    "爸": ["父", "巴"],
    "爷": ["父", "阝"],
    "妈": ["女", "马"],
    "姐": ["女", "且"],
    "妹": ["女", "未"],
    "奶": ["女", "乃"],
    "好": ["女", "子"],
    "她": ["女", "也"],
    "妈": ["女", "马"],
    "姑": ["女", "古"],
    "姓": ["女", "生"],
    "始": ["女", "台"],
    "婚": ["女", "昏"],
    "妈": ["女", "马"],
    "那": ["月", "阝"],
    "部": ["咅", "阝"],
    "都": ["者", "阝"],
    "陪": ["阝", "咅"],
    "阴": ["阝", "月"],
    "阳": ["阝", "日"],
    "院": ["阝", "完"],
    "除": ["阝", "余"],
    "陆": ["阝", "击"],
    "陈": ["阝", "东"],
    "防": ["阝", "方"],
    "阶": ["阝", "介"],
    "阵": ["阝", "车"],
    "阻": ["阝", "且"],
    "际": ["阝", "祭"],
    # 上下结构
    "字": ["宀", "子"],
    "宁": ["宀", "丁"],
    "它": ["宀", "匕"],
    "完": ["宀", "元"],
    "宋": ["宀", "木"],
    "宏": ["宀", "厷"],
    "牢": ["宀", "牛"],
    "灾": ["宀", "火"],
    "宝": ["宀", "玉"],
    "宗": ["宀", "示"],
    "官": ["宀", "㠯"],
    "定": ["宀", "正"],
    "宜": ["宀", "且"],
    "实": ["宀", "贯"],
    "客": ["宀", "各"],
    "室": ["宀", "至"],
    "宫": ["宀", "吕"],
    "家": ["宀", "豕"],
    "害": ["宀", "丰", "口"],
    "宽": ["宀", "苋"],
    "宁": ["宀", "丁"],
    "它": ["宀", "匕"],
    "花": ["艹", "化"],
    "草": ["艹", "早"],
    "茶": ["艹", "人", "木"],
    "菜": ["艹", "采"],
    "药": ["艹", "乐"],
    "苗": ["艹", "田"],
    "英": ["艹", "央"],
    "茂": ["艹", "戊"],
    "芳": ["艹", "方"],
    "苦": ["艹", "古"],
    "若": ["艹", "右"],
    "苹": ["艹", "平"],
    "节": ["艹", "卩"],
    "艺": ["艹", "乙"],
    "芒": ["艹", "亡"],
    "荷": ["艹", "何"],
    "莲": ["艹", "连"],
    "蓝": ["艹", "监"],
    "薄": ["艹", "溥"],
    "落": ["艹", "洛"],
    "藏": ["艹", "臧"],
    "蒙": ["艹", "冡"],
    "想": ["木", "目", "心"],
    "思": ["田", "心"],
    "怎": ["乍", "心"],
    "忽": ["勿", "心"],
    "急": ["刍", "心"],
    "忘": ["亡", "心"],
    "怕": ["忄", "白"],
    "忙": ["忄", "亡"],
    "快": ["忄", "夬"],
    "情": ["忄", "青"],
    "怪": ["忄", "圣"],
    "怜": ["忄", "令"],
    "性": ["忄", "生"],
    "感": ["咸", "心"],
    "悲": ["非", "心"],
    "愁": ["秋", "心"],
    "意": ["立", "曰", "心"],
    "愿": ["原", "心"],
    "忘": ["亡", "心"],
    "忘": ["亡", "心"],
    "尖": ["小", "大"],
    "尘": ["小", "土"],
    "尚": ["小", "向"],
    "当": ["⺌", "彐"],
    "省": ["少", "目"],
    "雀": ["小", "隹"],
    "劣": ["少", "力"],
    "岁": ["山", "夕"],
    "岸": ["山", "厂", "干"],
    "岩": ["山", "石"],
    "岳": ["丘", "山"],
    "密": ["宀", "山", "山", "虫"],
    "出": ["山", "山"],
    # 包围/半包围
    "国": ["囗", "玉"],
    "图": ["囗", "冬"],
    "园": ["囗", "元"],
    "困": ["囗", "木"],
    "团": ["囗", "才"],
    "围": ["囗", "韦"],
    "固": ["囗", "古"],
    "回": ["囗", "口"],
    "因": ["囗", "大"],
    "四": ["囗", "儿"],
    "圈": ["囗", "卷"],
    "冈": ["冂", "乂"],
    "同": ["冂", "一", "口"],
    "内": ["冂", "人"],
    "肉": ["冂", "人", "人"],
    "网": ["冂", "乂", "乂"],
    "问": ["门", "口"],
    "闻": ["门", "耳"],
    "间": ["门", "日"],
    "闭": ["门", "才"],
    "开": ["门", "开"],
    "闪": ["门", "人"],
    "闲": ["门", "木"],
    "闯": ["门", "马"],
    "阅": ["门", "兑"],
    "阁": ["门", "各"],
    "阔": ["门", "活"],
    "闷": ["门", "心"],
    "问": ["门", "口"],
    "这": ["辶", "文"],
    "过": ["辶", "寸"],
    "还": ["辶", "不"],
    "进": ["辶", "井"],
    "远": ["辶", "袁"],
    "近": ["辶", "斤"],
    "送": ["辶", "关"],
    "选": ["辶", "先"],
    "追": ["辶", "⺲"],
    "逃": ["辶", "兆"],
    "逆": ["辶", "屰"],
    "通": ["辶", "甬"],
    "道": ["辶", "首"],
    "遍": ["辶", "扁"],
    "达": ["辶", "大"],
    "迟": ["辶", "尺"],
    "速": ["辶", "束"],
    "连": ["辶", "车"],
    "迎": ["辶", "卬"],
    "运": ["辶", "云"],
    "边": ["辶", "力"],
    "赶": ["走", "干"],
    "起": ["走", "己"],
    "超": ["走", "召"],
    "越": ["走", "戉"],
    "趣": ["走", "取"],
    "趁": ["走", "㐱"],
    "赴": ["走", "卜"],
    "赵": ["走", "肖"],
    # 独体字 - 直接作为部件使用
    "人": [], "大": [], "小": [], "上": [], "下": [],
    "中": [], "左": [], "右": [], "不": [], "为": [],
    "之": [], "也": [], "已": [], "己": [], "久": [],
    "乎": [], "以": [], "及": [], "乃": [], "又": [],
}


def decompose_char(ch: str) -> list[str]:
    """
    递归拆字。返回最终不可再拆的基础部件(叶子)列表。

    例: decompose_char("想") -> ["木", "目", "心"]
        decompose_char("明") -> ["日", "月"]
        decompose_char("木") -> ["木"]  (本身就是根)
    """
    if ch in RADICAL_ROOT:
        return [ch]
    if ch in DECOMPOSE_RULES:
        parts = DECOMPOSE_RULES[ch]
        leaves: list[str] = []
        for p in parts:
            leaves.extend(decompose_char(p))
        return leaves
    # 无规则 -> 当作整体部件(可能是生僻字/不认识的字)
    return [ch]


# ============================================================================
# 2. 字形轮廓连通域自动拆解
# ============================================================================

@dataclass
class SubContour:
    """一个独立的子轮廓(可能是一个笔画或一个偏旁区域)"""

    contours: list[Contour] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def recompute_bbox(self) -> None:
        if not self.contours:
            self.bbox = (0.0, 0.0, 0.0, 0.0)
            return
        x_min = y_min = float("inf")
        x_max = y_max = float("-inf")
        for c in self.contours:
            for p in c.points:
                if p.x < x_min:
                    x_min = p.x
                if p.x > x_max:
                    x_max = p.x
                if p.y < y_min:
                    y_min = p.y
                if p.y > y_max:
                    y_max = p.y
        self.bbox = (x_min, y_min, x_max, y_max)


def _bbox_overlap(a: tuple[float, float, float, float],
                  b: tuple[float, float, float, float],
                  tol: float = 0.0) -> bool:
    a_xmin, a_ymin, a_xmax, a_ymax = a
    b_xmin, b_ymin, b_xmax, b_ymax = b
    return not (a_xmax + tol < b_xmin or b_xmax + tol < a_xmin or
                a_ymax + tol < b_ymin or b_ymax + tol < a_ymin)


def _contours_bbox(contours: Iterable[Contour]
                   ) -> tuple[float, float, float, float]:
    x_min = y_min = float("inf")
    x_max = y_max = float("-inf")
    found = False
    for c in contours:
        for p in c.points:
            found = True
            if p.x < x_min:
                x_min = p.x
            if p.x > x_max:
                x_max = p.x
            if p.y < y_min:
                y_min = p.y
            if p.y > y_max:
                y_max = p.y
    if not found:
        return (0.0, 0.0, 0.0, 0.0)
    return (x_min, y_min, x_max, y_max)


def contour_decompose(glyph: GlyphContours,
                      ) -> list[SubContour]:
    """
    把一个字形拆成若干独立子轮廓组。
    规则: 若两个 contour 的包围盒不相交，它们属于不同的部件。

    先按粗略的包围盒分组（可后续扩展为点在多边形内的精确检测）。

    返回的每个 SubContour 内部的轮廓之间是"连接的"(包围盒相交)。
    """
    if glyph.is_empty():
        return []

    contours = glyph.contours
    n = len(contours)
    # 给每个 contour 预先算 bbox
    bboxes: list[tuple[float, float, float, float]] = []
    for c in contours:
        bboxes.append(_contours_bbox([c]))

    # 并查集 union-find
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

    # 检测每个 contour 之间是否 bbox 重叠
    for i in range(n):
        for j in range(i + 1, n):
            if _bbox_overlap(bboxes[i], bboxes[j], tol=0.0):
                union(i, j)

    # 收集分组
    groups: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)

    result: list[SubContour] = []
    for indices in groups.values():
        sc = SubContour(contours=[contours[i] for i in indices])
        sc.recompute_bbox()
        result.append(sc)

    # 按面积从大到小排序（主部件先看）
    result.sort(key=lambda s: -(
        (s.bbox[2] - s.bbox[0]) * (s.bbox[3] - s.bbox[1])
    ))
    return result


# ============================================================================
# 3. 部件归一化 + 精确重叠匹配
# ============================================================================

@dataclass
class NormalizedContour:
    """归一化后的子轮廓（用于比较两个部件是否几何一致）"""

    points: list[tuple[float, float]]  # (x,y) 已归一化到 [0,1]^2
    bbox: tuple[float, float, float, float]  # 原始 bbox
    size: float  # 原始大小 (max(w,h))
    cx: float    # 原始中心 x
    cy: float    # 原始中心 y


def normalize(sub: SubContour) -> NormalizedContour:
    """
    将一个子轮廓组归一化:
      1. 平移: 使中心在 (0,0)
      2. 缩放: 使最大边 = 1.0
      3. 翻转一致: 使左上象限点数 >= 右下象限点数 (保证镜像一致)
      4. 重新平移: 使包围盒左下角在 (0,0)（便于比较）
    """
    if not sub.contours:
        return NormalizedContour([], (0, 0, 0, 0), 0.0, 0.0, 0.0)

    # 取所有点
    pts: list[tuple[float, float]] = []
    for c in sub.contours:
        for p in c.points:
            pts.append((float(p.x), float(p.y)))

    x_min = min(p[0] for p in pts)
    y_min = min(p[1] for p in pts)
    x_max = max(p[0] for p in pts)
    y_max = max(p[1] for p in pts)
    cx = (x_min + x_max) / 2.0
    cy = (y_min + y_max) / 2.0
    size = max(x_max - x_min, y_max - y_min)
    if size <= 0:
        size = 1.0

    # 平移到中心，缩放到单位大小
    centered: list[tuple[float, float]] = [
        ((p[0] - cx) / size, (p[1] - cy) / size) for p in pts
    ]

    # 翻转一致: 统计左上(-,-) vs 右下(+,+)（这里按 x<0 and y>0 vs x>0 and y<0）
    # 我们采用简单规则：若质心 x<0 则整体水平翻转，若质心 y<0 则整体垂直翻转
    mean_x = sum(p[0] for p in centered) / len(centered)
    mean_y = sum(p[1] for p in centered) / len(centered)
    fixed: list[tuple[float, float]] = []
    for p in centered:
        nx = -p[0] if mean_x < 0 else p[0]
        ny = -p[1] if mean_y < 0 else p[1]
        fixed.append((nx, ny))

    # 再平移到 [0, ?] 空间
    fx_min = min(p[0] for p in fixed)
    fy_min = min(p[1] for p in fixed)
    fx_max = max(p[0] for p in fixed)
    fy_max = max(p[1] for p in fixed)
    w = fx_max - fx_min or 1.0
    h = fy_max - fy_min or 1.0
    s = max(w, h)
    final = [((p[0] - fx_min) / s, (p[1] - fy_min) / s) for p in fixed]

    return NormalizedContour(
        points=final,
        bbox=(x_min, y_min, x_max, y_max),
        size=size,
        cx=cx,
        cy=cy,
    )


def _nearest_distance(p: tuple[float, float],
                      other_points: list[tuple[float, float]]) -> float:
    """点 p 到 other_points 中最近点的距离（暴力，用于小规模轮廓）"""
    best = float("inf")
    for q in other_points:
        dx = p[0] - q[0]
        dy = p[1] - q[1]
        d2 = dx * dx + dy * dy
        if d2 < best:
            best = d2
        if best == 0:
            return 0.0
    return math.sqrt(best)


def _hausdorff(a: list[tuple[float, float]],
               b: list[tuple[float, float]]) -> float:
    if not a or not b:
        return float("inf")
    max_d_ab = max(_nearest_distance(p, b) for p in a)
    max_d_ba = max(_nearest_distance(p, a) for p in b)
    return max(max_d_ab, max_d_ba)


@dataclass
class ComponentMatchResult:
    """两个子轮廓的匹配结果"""

    match: bool
    similarity: float  # 1.0 - hausdorff / diag  (越大越相似)
    hausdorff: float   # 归一化坐标中的 Hausdorff 距离
    note: str = ""


def components_match(sub_a: SubContour, sub_b: SubContour,
                     tolerance: float = 0.05) -> ComponentMatchResult:
    """
    判断两个子轮廓是否是同一个部件（几何完全一致/几乎一致）。

    过程:
      1. 分别 normalize 到 [0,1]^2（归一化大小 + 翻转一致）
      2. 计算 Hausdorff 距离（双侧）
      3. 距离 <= tolerance → 判定为共用部件

    tolerance: 归一化单位下允许的"不重合"距离，默认 0.05 (=5%)
               即: 若两个部件的任何一点，对应位置不超过整体大小 5% 的偏差，
               就认为是同一部件。
    """
    na = normalize(sub_a)
    nb = normalize(sub_b)
    if not na.points or not nb.points:
        return ComponentMatchResult(False, 0.0, float("inf"), "空轮廓")

    # 采样点数量对齐（太大时降采样），避免超级慢
    MAX_POINTS = 200
    def _sample(pts: list[tuple[float, float]]
                ) -> list[tuple[float, float]]:
        if len(pts) <= MAX_POINTS:
            return pts
        step = len(pts) / MAX_POINTS
        return [pts[int(i * step)] for i in range(MAX_POINTS)]

    pa = _sample(na.points)
    pb = _sample(nb.points)

    d = _hausdorff(pa, pb)
    # 相似度 = 1 - d / 单位对角线
    diag = math.sqrt(2.0)
    similarity = max(0.0, 1.0 - d / diag)
    return ComponentMatchResult(
        match=(d <= tolerance),
        similarity=similarity,
        hausdorff=d,
    )


# ============================================================================
# 4. 高层: 对一个字形跑"完整分析"
# ============================================================================

@dataclass
class GlyphDecomposeResult:
    """一个字的分解结果"""

    char: str
    unicode_val: int
    rules: list[str]              # 拆字规则预测的部件（汉字）
    sub_contours: list[SubContour]  # 几何拆分出的子轮廓组
    matched_components: dict[str, SubContour]  # 部件标签 -> 对应子轮廓(如已对齐)


def decompose_glyph(glyph: GlyphContours,
                    rules: list[str] | None = None,
                    ) -> GlyphDecomposeResult:
    """
    高层接口: 对一个 glyph 做"规则 + 几何"联合拆分。

    目前:
      - rules 优先按 DECOMPOSE_RULES 的拆字规则决定应有几个部件；
      - sub_contours 由几何连通域给出实际轮廓分组；
      - 后续可以把 sub_contours 一一对应到规则部件(按相对位置)。
    """
    ch = chr(glyph.unicode) if 0 < glyph.unicode < 0x110000 else "?"
    if rules is None:
        rules = decompose_char(ch)
    subs = contour_decompose(glyph)
    return GlyphDecomposeResult(
        char=ch,
        unicode_val=glyph.unicode,
        rules=rules,
        sub_contours=subs,
        matched_components={},
    )


@dataclass
class SharedComponent:
    """跨字共享的部件"""

    tag: str                           # 内部编号，如 "part_001"
    representative: SubContour         # 代表轮廓（选出现次数最多的那一个）
    chars: list[str]                   # 用到这个部件的字列表（字符）
    appearance_count: int              # 出现次数
    mean_similarity: float             # 平均相似度 (>=0.95 才认为是"完全重合")


def find_shared_components(glyphs: list[GlyphContours],
                           tolerance: float = 0.05,
                           ) -> tuple[dict[str, list[SubContour]],
                                      list[SharedComponent]]:
    """
    在一堆字形中找出"几何一致的共享部件"。

    返回:
      - char_subs: { "明": [sub1, sub2, ...] } 每个字的子轮廓列表
      - shared:     按匹配分组得到的共享部件（按出现次数倒序）
    """
    char_subs: dict[str, list[SubContour]] = {}
    all_subs: list[tuple[str, SubContour]] = []  # (字, 子轮廓)

    for g in glyphs:
        ch = chr(g.unicode) if 0 < g.unicode < 0x110000 else f"U{g.unicode:04X}"
        subs = contour_decompose(g)
        char_subs[ch] = subs
        for s in subs:
            all_subs.append((ch, s))

    # 两两聚类（暴力，适合几百个子轮廓；更大规模需要先按签名粗分）
    # 这里用并查集分组
    n = len(all_subs)
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

    # 记录每组相似度
    pair_sim: dict[int, list[float]] = {}

    # 为避免 O(N^2) 爆掉，先做 bbox 大小的粗过滤: 只有大小差异在 2x
    # 内的两个子轮廓才做精确 Hausdorff 比对
    for i in range(n):
        _, sa = all_subs[i]
        sa_size = max(sa.bbox[2] - sa.bbox[0],
                      sa.bbox[3] - sa.bbox[1]) or 1.0
        for j in range(i + 1, n):
            _, sb = all_subs[j]
            sb_size = max(sb.bbox[2] - sb.bbox[0],
                          sb.bbox[3] - sb.bbox[1]) or 1.0
            ratio = sa_size / sb_size
            if ratio < 0.33 or ratio > 3.0:
                continue  # 大小差异太大，不可能是同一部件
            r = components_match(sa, sb, tolerance=tolerance)
            if r.match:
                union(i, j)
                root = find(i)
                pair_sim.setdefault(root, []).append(r.similarity)

    # 收集分组
    groups: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)

    shared: list[SharedComponent] = []
    for root, indices in groups.items():
        if len(indices) <= 1:
            continue  # 只出现一次的不叫"共享"
        subs_in_group = [all_subs[i][1] for i in indices]
        chars_in_group = sorted({all_subs[i][0] for i in indices})
        # 代表: 选面积中位数的那个
        subs_in_group.sort(key=lambda s: (
            (s.bbox[2] - s.bbox[0]) * (s.bbox[3] - s.bbox[1])
        ))
        rep = subs_in_group[len(subs_in_group) // 2]
        sims = pair_sim.get(root, [1.0])
        mean_sim = sum(sims) / len(sims) if sims else 1.0
        shared.append(SharedComponent(
            tag=f"part_{len(shared):03d}",
            representative=rep,
            chars=chars_in_group,
            appearance_count=len(indices),
            mean_similarity=mean_sim,
        ))

    shared.sort(key=lambda s: -s.appearance_count)
    return char_subs, shared
