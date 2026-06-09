"""
模块2: 部件库初始化器

负责从汉字字体文件中提取/构建部件（偏旁/部首）库，作为后续匹配的基础。

部件库包含:
1. 常见部首（氵、木、口、亻等）的典型轮廓样本
2. 每个部首可能有多个形态样本（因为同一偏旁在不同字中形态不同）
3. 每个部件有唯一ID和语义名称

实现说明:
- 初始化方式一: 从部首表定义的区域自动提取样本字中的部件
- 初始化方式二: 从用户提供的 (unicode, part_unicode) 对中提取部件
- 每个部件保留多个形态样本以便后续匹配

参考: docs/technical-design.md 第 3.2 节 (Component / ComponentLibrary)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

from .glyph_extractor import Contour, GlyphContours, GlyphExtractor, GlyphExtractResult


# 常见部首列表 (康熙部首前 50 个, Unicode: U+2F00..U+2F3F)
# 同时列出一些常用偏旁
COMMON_RADICALS = [
    "一", "丨", "丶", "丿", "乙", "亅", "二", "亠", "人", "儿",
    "入", "八", "冂", "冖", "冫", "几", "凵", "刀", "力", "勹",
    "匕", "匚", "十", "卜", "卩", "厂", "厶", "又", "口", "囗",
    "土", "士", "夂", "夊", "夕", "大", "女", "子", "宀", "寸",
    "小", "尢", "尸", "屮", "山", "川", "工", "己", "巾", "干",
    "广", "廴", "廾", "弋", "弓", "彐", "彡", "彳", "心", "戈",
    "户", "手", "支", "攵", "文", "斗", "斤", "方", "无", "日",
    "曰", "月", "木", "欠", "止", "歹", "殳", "毋", "比", "毛",
    "氏", "气", "水", "火", "爪", "父", "爻", "片", "牛", "犬",
    "玄", "玉", "瓜", "瓦", "甘", "生", "用", "田", "疋", "疒",
    "癶", "白", "皮", "皿", "目", "矛", "矢", "石", "示", "禸",
    "禾", "穴", "立", "竹", "米", "糸", "缶", "羊", "羽", "老",
    "而", "耒", "耳", "聿", "肉", "臣", "自", "至", "臼", "舌",
    "舛", "舟", "艮", "色", "艸", "虍", "虫", "血", "行", "衣",
    "西", "见", "角", "言", "谷", "豆", "豕", "豸", "贝", "赤",
    "走", "足", "身", "车", "辛", "辰", "辵", "邑", "酉", "釆",
    "里", "金", "长", "门", "阜", "隶", "隹", "雨", "青", "非",
    "面", "革", "韦", "韭", "音", "页", "风", "飞", "食", "首",
    "香", "马", "骨", "高", "髟", "斗", "鬯", "鬲", "鬼", "鱼",
    "鸟", "鹿", "麦", "麻", "黄", "黍", "黑", "黹", "黾", "鼎",
    "鼓", "鼠", "鼻", "齐", "齿", "龙", "龟", "龠",
]


# 常用偏旁列表（用于补充部首中缺失的常用偏旁）
COMMON_RADICALS_AS_PARTS = [
    "氵", "亻", "扌", "艹", "讠", "钅", "饣", "纟", "衤", "礻",
    "犭", "忄", "木", "火", "土", "日", "月", "目", "口", "田",
    "女", "子", "艹", "冫", "灬", "人", "马", "鸟", "虫", "鱼",
    "贝", "车", "门", "钅", "氵", "女", "口", "日", "月",
]


@dataclass
class Component:
    """
    一个部件（偏旁/部首）。

    Attributes:
        id: 唯一ID (如 "radical_氵" 或 "part_001")
        name: 语义名称 (如 "三点水"、"木字旁")
        semantic: 对应的 Unicode 字符 (如 "氵")
        contour_samples: 多个形态的轮廓样本 (同一偏旁的不同写法)
        usage_count: 被多少字引用（用于排序/优化）
        notes: 备注信息
    """

    id: str
    name: str
    semantic: str = ""
    contour_samples: list[list[Contour]] = field(default_factory=list)
    usage_count: int = 0
    notes: str = ""

    def add_sample(self, contours: list[Contour]) -> None:
        """添加一个形态样本"""
        if contours and any(len(c) > 0 for c in contours):
            self.contour_samples.append(contours)

    def add_sample_from_glyph(self, glyph: GlyphContours) -> None:
        """从字形数据添加样本"""
        if glyph.contours:
            self.contour_samples.append(glyph.contours)

    def num_samples(self) -> int:
        """返回样本数量"""
        return len(self.contour_samples)

    def has_samples(self) -> bool:
        """是否有样本数据"""
        return len(self.contour_samples) > 0

    def get_bounding_box(
        self, sample_index: int = 0
    ) -> Optional[tuple[float, float, float, float]]:
        """获取指定样本的边界框"""
        if sample_index >= len(self.contour_samples):
            return None

        sample = self.contour_samples[sample_index]
        if not sample:
            return None

        all_points: list[tuple[float, float]] = []
        for contour in sample:
            for pt in contour:
                all_points.append((pt.x / 64.0, pt.y / 64.0))

        if not all_points:
            return None

        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "name": self.name,
            "semantic": self.semantic,
            "usage_count": self.usage_count,
            "notes": self.notes,
            "num_samples": len(self.contour_samples),
        }


@dataclass
class ComponentLibrary:
    """
    部件库。

    包含多个部件，每个部件有多个形态样本。

    Usage:
        library = ComponentLibrary()
        library.add_component(Component(id="...", name="..."))
        print(len(library))
    """

    components: dict[str, Component] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.components)

    def __iter__(self):
        return iter(self.components.values())

    def __contains__(self, component_id: str) -> bool:
        return component_id in self.components

    def __getitem__(self, component_id: str) -> Component:
        return self.components[component_id]

    def add_component(self, component: Component) -> None:
        """添加部件"""
        if component.id not in self.components:
            self.components[component.id] = component

    def get_component(self, component_id: str) -> Optional[Component]:
        """获取指定部件"""
        return self.components.get(component_id)

    def get_component_by_semantic(self, semantic: str) -> Optional[Component]:
        """通过语义名称查找部件"""
        for comp in self.components.values():
            if comp.semantic == semantic:
                return comp
        return None

    def add_sample(self, component_id: str, contours: list[Contour]) -> bool:
        """给指定部件添加样本"""
        comp = self.get_component(component_id)
        if comp is None:
            return False
        comp.add_sample(contours)
        return True

    def total_samples(self) -> int:
        """总样本数"""
        return sum(c.num_samples() for c in self.components.values())

    def components_with_samples(self) -> list[Component]:
        """返回有样本的部件列表"""
        return [c for c in self.components.values() if c.has_samples()]

    def components_by_usage(self) -> list[Component]:
        """按使用次数排序"""
        return sorted(self.components.values(), key=lambda c: c.usage_count, reverse=True)

    def to_dict(self) -> dict:
        """转换为字典摘要"""
        return {
            "num_components": len(self.components),
            "total_samples": self.total_samples(),
            "components": [c.to_dict() for c in self.components.values()],
        }

    def summary(self) -> str:
        """生成摘要文本"""
        total = len(self.components)
        with_samples = len(self.components_with_samples())
        total_s = self.total_samples()
        return (
            f"部件库: {total} 个部件, "
            f"其中 {with_samples} 个有样本数据, "
            f"共 {total_s} 个形态样本"
        )

    def save(self, path: str | Path) -> None:
        """保存部件库元数据（注意: 不包含轮廓数据，仅用于调试）"""
        data = self.to_dict()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_summary(cls, path: str | Path) -> "ComponentLibrary":
        """从摘要JSON加载部件库元数据"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        library = cls()
        for item in data.get("components", []):
            comp = Component(
                id=item["id"],
                name=item["name"],
                semantic=item.get("semantic", ""),
                usage_count=item.get("usage_count", 0),
                notes=item.get("notes", ""),
            )
            library.add_component(comp)
        return library


@dataclass
class ComponentLibraryConfig:
    """部件库初始化配置"""

    # 是否只提取常用汉字中的部首
    common_chars_only: bool = True

    # 每个部件最多保留的样本数
    max_samples_per_component: int = 5

    # 样本字的最小笔画数（过滤过于简单的字）
    min_strokes: int = 1

    # 是否自动去重（相同轮廓只保留一个样本）
    dedupe_samples: bool = True

    # 相似度阈值（用于去重判断，0-1）
    similarity_threshold: float = 0.95

    # 是否包含扩展部首
    include_extended_radicals: bool = False


class ComponentLibraryInitializer:
    """
    部件库初始化器。

    从字体文件和部首数据构建部件库。

    工作流程:
    1. 加载部首表（从 COMMON_RADICALS 或自定义列表）
    2. 从字体文件中提取每个部首作为独立字形
    3. 为每个部件保留最多 N 个形态样本

    Usage:
        initializer = ComponentLibraryInitializer()
        library = initializer.build_from_font("font.ttf")
        print(library.summary())
    """

    def __init__(self, config: ComponentLibraryConfig | None = None):
        self.config = config or ComponentLibraryConfig()

    def _build_default_component_list(self) -> list[tuple[str, str, str]]:
        """
        构建默认部件列表。

        Returns:
            [(component_id, name, semantic_char), ...]
        """
        components = []
        seen = set()

        # 1. 先添加常用偏旁
        for char in COMMON_RADICALS_AS_PARTS:
            if char not in seen:
                seen.add(char)
                cid = f"part_{ord(char):04X}"
                components.append((cid, char, char))

        # 2. 再补充部首
        for char in COMMON_RADICALS:
            if char not in seen:
                seen.add(char)
                cid = f"radical_{ord(char):04X}"
                components.append((cid, char, char))

        return components

    def _extract_glyph_contours(
        self,
        extractor: GlyphExtractor,
        unicode_val: int,
    ) -> Optional[GlyphContours]:
        """
        从字体文件提取指定字符的轮廓。

        Returns:
            GlyphContours 或 None（如果提取失败）
        """
        # 将在 build_from_font 中实现，这里保留接口定义
        pass

    def build_empty_library(
        self,
        component_list: Iterable[tuple[str, str, str]] | None = None,
    ) -> ComponentLibrary:
        """
        构建空部件库（只包含部件定义，没有轮廓样本）。

        Args:
            component_list: 可选的自定义部件列表
                           格式: [(component_id, name, semantic_char), ...]

        Returns:
            ComponentLibrary
        """
        if component_list is None:
            component_list = self._build_default_component_list()

        library = ComponentLibrary()
        for cid, name, semantic in component_list:
            comp = Component(id=cid, name=name, semantic=semantic)
            library.add_component(comp)

        return library

    def build_from_font(
        self,
        font_path: str | Path,
        component_list: Iterable[tuple[str, str, str]] | None = None,
    ) -> ComponentLibrary:
        """
        从字体文件构建部件库（包含轮廓样本）。

        Args:
            font_path: 字体文件路径 (TTF/OTF)
            component_list: 可选的自定义部件列表

        Returns:
            ComponentLibrary
        """
        # 1. 构建空部件库
        library = self.build_empty_library(component_list)

        # 2. 创建字形提取器
        extractor = GlyphExtractor()

        # 3. 为每个部件提取样本
        for comp in library:
            if not comp.semantic:
                continue

            unicode_val = ord(comp.semantic)
            if unicode_val <= 0:
                continue

            # 提取轮廓
            result = extractor.extract_from_file(font_path, unicode_val)
            if result.success and result.glyph.contours:
                comp.add_sample_from_glyph(result.glyph)
                comp.usage_count = 1

        return library

    def build_from_glyph_dict(
        self,
        glyph_dict: dict[int, GlyphContours],
        component_list: Iterable[tuple[str, str, str]] | None = None,
    ) -> ComponentLibrary:
        """
        从已有的字形字典构建部件库。

        Args:
            glyph_dict: {unicode: GlyphContours}
            component_list: 可选的自定义部件列表

        Returns:
            ComponentLibrary
        """
        library = self.build_empty_library(component_list)

        for comp in library:
            if not comp.semantic:
                continue

            unicode_val = ord(comp.semantic)
            if unicode_val in glyph_dict:
                glyph = glyph_dict[unicode_val]
                if glyph.contours:
                    comp.add_sample_from_glyph(glyph)
                    comp.usage_count = 1

        return library

    def add_sample_to_component(
        self,
        library: ComponentLibrary,
        component_id: str,
        glyph: GlyphContours,
    ) -> bool:
        """
        给部件库中指定部件添加一个样本。

        Args:
            library: 部件库
            component_id: 部件ID
            glyph: 字形轮廓数据

        Returns:
            bool: 是否成功添加
        """
        comp = library.get_component(component_id)
        if comp is None:
            return False

        # 检查样本数量限制
        if comp.num_samples() >= self.config.max_samples_per_component:
            return False

        if not glyph.contours:
            return False

        comp.add_sample_from_glyph(glyph)
        return True


# 便捷函数

def build_default_component_list() -> list[tuple[str, str, str]]:
    """返回默认部件列表"""
    initializer = ComponentLibraryInitializer()
    return initializer._build_default_component_list()


def create_library_from_font(
    font_path: str | Path,
    config: ComponentLibraryConfig | None = None,
) -> ComponentLibrary:
    """
    便捷函数: 从字体文件创建部件库。

    Args:
        font_path: 字体文件路径
        config: 可选配置

    Returns:
        ComponentLibrary
    """
    initializer = ComponentLibraryInitializer(config=config)
    return initializer.build_from_font(font_path)
