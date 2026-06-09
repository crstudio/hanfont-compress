"""
测试模块2: 部件库 (ComponentLibrary)

Usage:
    pytest tests/test_component_library.py -v
"""

import sys
import tempfile
from pathlib import Path

import pytest

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hfc.component_library import (
    COMMON_RADICALS,
    Component,
    ComponentLibrary,
    ComponentLibraryConfig,
    ComponentLibraryInitializer,
    build_default_component_list,
    create_library_from_font,
)
from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours


class TestComponent:
    """测试 Component 数据类"""

    def test_creation(self):
        """测试创建部件"""
        comp = Component(id="part_001", name="氵", semantic="氵")
        assert comp.id == "part_001"
        assert comp.name == "氵"
        assert comp.semantic == "氵"
        assert comp.num_samples() == 0
        assert comp.has_samples() is False

    def test_add_sample_from_contours(self):
        """测试添加轮廓样本"""
        comp = Component(id="part_001", name="氵", semantic="氵")

        contour = Contour()
        contour.add_point(0, 0, True)
        contour.add_point(6400, 6400, True)

        comp.add_sample([contour])

        assert comp.num_samples() == 1
        assert comp.has_samples() is True

    def test_add_sample_from_glyph(self):
        """测试从字形添加样本"""
        comp = Component(id="part_002", name="木", semantic="木")

        glyph = GlyphContours(unicode=0x6728, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)

        comp.add_sample_from_glyph(glyph)

        assert comp.num_samples() == 1

    def test_add_empty_sample_ignored(self):
        """测试空样本被忽略"""
        comp = Component(id="part_003", name="口", semantic="口")

        # 空轮廓列表应该被忽略
        comp.add_sample([])
        assert comp.num_samples() == 0

        # 包含空轮廓也应该被忽略
        empty_contour = Contour()
        comp.add_sample([empty_contour])
        assert comp.num_samples() == 0

    def test_bounding_box(self):
        """测试获取边界框"""
        comp = Component(id="part_004", name="日", semantic="日")

        glyph = GlyphContours(unicode=0x65E5, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 0, True)
        c.add_point(6400, 6400, True)
        c.add_point(0, 6400, True)
        comp.add_sample_from_glyph(glyph)

        bbox = comp.get_bounding_box()
        assert bbox is not None
        x_min, y_min, x_max, y_max = bbox
        assert x_min >= 0
        assert x_max <= 101
        assert abs(x_max - x_min) > 0

    def test_bounding_box_empty(self):
        """测试空部件返回 None 边界框"""
        comp = Component(id="part_005", name="空", semantic="")
        assert comp.get_bounding_box() is None

    def test_usage_count(self):
        """测试使用次数计数"""
        comp = Component(id="part_006", name="亻", semantic="亻", usage_count=100)
        assert comp.usage_count == 100
        comp.usage_count += 1
        assert comp.usage_count == 101

    def test_to_dict(self):
        """测试字典序列化"""
        comp = Component(
            id="part_007", name="钅", semantic="钅", usage_count=50, notes="金属类"
        )
        d = comp.to_dict()

        assert d["id"] == "part_007"
        assert d["name"] == "钅"
        assert d["semantic"] == "钅"
        assert d["usage_count"] == 50
        assert d["notes"] == "金属类"


class TestComponentLibrary:
    """测试 ComponentLibrary 部件库"""

    def test_empty_library(self):
        """测试空部件库"""
        library = ComponentLibrary()
        assert len(library) == 0
        assert library.total_samples() == 0
        assert library.components_with_samples() == []

    def test_add_component(self):
        """测试添加部件"""
        library = ComponentLibrary()
        comp = Component(id="part_001", name="氵", semantic="氵")
        library.add_component(comp)

        assert len(library) == 1
        assert "part_001" in library

    def test_duplicate_component_ignored(self):
        """测试重复添加部件被忽略"""
        library = ComponentLibrary()
        comp1 = Component(id="part_001", name="氵", semantic="氵")
        comp2 = Component(id="part_001", name="三点水", semantic="氵")

        library.add_component(comp1)
        library.add_component(comp2)

        assert len(library) == 1
        assert library["part_001"].name == "氵"  # 保留第一个

    def test_get_component(self):
        """测试获取部件"""
        library = ComponentLibrary()
        comp = Component(id="part_001", name="氵", semantic="氵")
        library.add_component(comp)

        assert library.get_component("part_001") is comp
        assert library.get_component("unknown") is None

    def test_get_component_by_semantic(self):
        """测试通过语义字符查找部件"""
        library = ComponentLibrary()
        comp1 = Component(id="part_001", name="氵", semantic="氵")
        comp2 = Component(id="part_002", name="木", semantic="木")
        library.add_component(comp1)
        library.add_component(comp2)

        assert library.get_component_by_semantic("氵") is comp1
        assert library.get_component_by_semantic("木") is comp2
        assert library.get_component_by_semantic("不存在") is None

    def test_add_sample_to_component(self):
        """测试给部件添加样本"""
        library = ComponentLibrary()
        comp = Component(id="part_001", name="氵", semantic="氵")
        library.add_component(comp)

        glyph = GlyphContours(unicode=0x6C34, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 6400, True)

        result = library.add_sample("part_001", [c])
        assert result is True
        assert library.total_samples() == 1

    def test_add_sample_to_nonexistent(self):
        """测试给不存在的部件添加样本"""
        library = ComponentLibrary()
        result = library.add_sample("nonexistent", [])
        assert result is False

    def test_components_with_samples(self):
        """测试获取有样本的部件列表"""
        library = ComponentLibrary()

        comp1 = Component(id="p1", name="氵", semantic="氵")
        comp2 = Component(id="p2", name="木", semantic="木")
        library.add_component(comp1)
        library.add_component(comp2)

        # 给 comp1 添加样本
        glyph = GlyphContours(unicode=0x6C34, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 6400, True)
        comp1.add_sample_from_glyph(glyph)

        with_samples = library.components_with_samples()
        assert len(with_samples) == 1
        assert with_samples[0].id == "p1"

    def test_components_by_usage(self):
        """测试按使用次数排序"""
        library = ComponentLibrary()

        for i in range(3):
            comp = Component(id=f"p{i}", name=f"部件{i}", semantic="", usage_count=i * 10)
            library.add_component(comp)

        sorted_comps = library.components_by_usage()
        assert sorted_comps[0].usage_count == 20
        assert sorted_comps[1].usage_count == 10
        assert sorted_comps[2].usage_count == 0

    def test_iteration(self):
        """测试迭代"""
        library = ComponentLibrary()
        library.add_component(Component(id="p1", name="氵", semantic="氵"))
        library.add_component(Component(id="p2", name="木", semantic="木"))
        library.add_component(Component(id="p3", name="口", semantic="口"))

        ids = [c.id for c in library]
        assert set(ids) == {"p1", "p2", "p3"}

    def test_summary(self):
        """测试摘要信息"""
        library = ComponentLibrary()
        comp = Component(id="p1", name="氵", semantic="氵")
        library.add_component(comp)

        # 添加样本
        glyph = GlyphContours(unicode=0x6C34, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 6400, True)
        comp.add_sample_from_glyph(glyph)

        summary = library.summary()
        assert "部件库" in summary
        assert "1" in summary
        print(f"\nSummary: {summary}")

    def test_save_and_load(self):
        """测试保存和加载部件库元数据"""
        library = ComponentLibrary()
        for i in range(3):
            comp = Component(
                id=f"p{i}", name=f"部件{i}", semantic=f"字{i}", usage_count=i * 10
            )
            library.add_component(comp)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            library.save(str(path))

            assert path.exists()

            # 加载摘要
            loaded = ComponentLibrary.load_summary(str(path))
            assert len(loaded) == 3
            # 注意: 轮廓样本不会被序列化保存
            # 只保存元数据

            # 检查元数据
            assert loaded.get_component("p0").usage_count == 0
            assert loaded.get_component("p1").usage_count == 10

    def test_to_dict(self):
        """测试字典转换"""
        library = ComponentLibrary()
        comp = Component(id="p1", name="氵", semantic="氵", usage_count=5)
        library.add_component(comp)

        d = library.to_dict()
        assert d["num_components"] == 1
        assert d["total_samples"] == 0
        assert len(d["components"]) == 1
        assert d["components"][0]["id"] == "p1"


class TestComponentLibraryConfig:
    """测试配置类"""

    def test_defaults(self):
        """测试默认配置"""
        config = ComponentLibraryConfig()
        assert config.common_chars_only is True
        assert config.max_samples_per_component == 5
        assert config.min_strokes == 1
        assert config.dedupe_samples is True
        assert config.similarity_threshold == 0.95
        assert config.include_extended_radicals is False

    def test_custom(self):
        """测试自定义配置"""
        config = ComponentLibraryConfig(
            max_samples_per_component=10,
            similarity_threshold=0.9,
            include_extended_radicals=True,
        )
        assert config.max_samples_per_component == 10
        assert config.similarity_threshold == 0.9
        assert config.include_extended_radicals is True


class TestComponentLibraryInitializer:
    """测试部件库初始化器"""

    def test_default_component_list(self):
        """测试默认部件列表"""
        initializer = ComponentLibraryInitializer()
        components = initializer._build_default_component_list()

        # 应该有一些部件
        assert len(components) > 20

        # 检查格式
        for cid, name, semantic in components:
            assert cid.startswith("part_") or cid.startswith("radical_")
            assert name
            assert semantic  # 应该是汉字

        print(f"\n默认部件数量: {len(components)}")

    def test_common_radicals_available(self):
        """测试常用部首是否在列表中"""
        initializer = ComponentLibraryInitializer()
        components = initializer._build_default_component_list()

        # 检查一些常见偏旁
        part_names = [name for _, name, _ in components]

        # 氵 (三点水) 应该存在
        assert "氵" in part_names
        # 亻 (单人旁) 应该存在
        assert "亻" in part_names
        # 木 (木字旁) 应该存在
        assert "木" in part_names

    def test_build_empty_library(self):
        """测试构建空部件库"""
        initializer = ComponentLibraryInitializer()
        library = initializer.build_empty_library()

        assert len(library) > 20
        assert library.total_samples() == 0

    def test_build_empty_library_custom(self):
        """测试使用自定义列表构建空部件库"""
        initializer = ComponentLibraryInitializer()
        custom_list = [
            ("custom_001", "三点水", "氵"),
            ("custom_002", "木字旁", "木"),
        ]

        library = initializer.build_empty_library(custom_list)
        assert len(library) == 2
        assert library.get_component("custom_001") is not None
        assert library.get_component("custom_002") is not None

    def test_build_from_glyph_dict(self):
        """测试从字形字典构建部件库"""
        initializer = ComponentLibraryInitializer()

        # 准备几个字形作为样本
        glyph_dict = {}

        # "氵" (U+6C35) - 创建一个简单轮廓
        glyph_shui = GlyphContours(unicode=0x6C35, bbox=(0, 0, 6400, 6400))
        c = glyph_shui.add_contour()
        c.add_point(1000, 5000, True)
        c.add_point(1500, 3000, True)
        c.add_point(2000, 1000, True)
        glyph_dict[0x6C35] = glyph_shui

        # "木" (U+6728) - 创建一个简单轮廓
        glyph_mu = GlyphContours(unicode=0x6728, bbox=(0, 0, 6400, 6400))
        c = glyph_mu.add_contour()
        c.add_point(0, 3000, True)
        c.add_point(6400, 3000, True)
        c.add_point(3200, 0, True)
        c.add_point(3200, 6400, True)
        glyph_dict[0x6728] = glyph_mu

        # 自定义部件列表，只包含这两个
        custom_list = [
            ("part_氵", "三点水", "氵"),
            ("part_木", "木字旁", "木"),
        ]

        library = initializer.build_from_glyph_dict(glyph_dict, custom_list)
        assert len(library) == 2

        # 应该有样本了
        comp_shui = library.get_component_by_semantic("氵")
        assert comp_shui is not None
        assert comp_shui.has_samples() is True

        comp_mu = library.get_component_by_semantic("木")
        assert comp_mu is not None
        assert comp_mu.has_samples() is True

    def test_add_sample_with_limit(self):
        """测试样本数量限制"""
        config = ComponentLibraryConfig(max_samples_per_component=3)
        initializer = ComponentLibraryInitializer(config=config)

        library = initializer.build_empty_library([("p1", "测试", "氵")])
        comp = library.get_component("p1")

        # 添加4个样本，只有前3个应该成功
        for i in range(5):
            glyph = GlyphContours(unicode=0x6C35, bbox=(0, 0, 6400, 6400))
            c = glyph.add_contour()
            c.add_point(i * 100, i * 100, True)
            c.add_point(i * 200, i * 200, True)

            result = initializer.add_sample_to_component(library, "p1", glyph)

        # 最多只能有 max_samples_per_component 个
        assert comp.num_samples() <= 3

    def test_add_sample_to_nonexistent(self):
        """测试给不存在的部件添加样本"""
        initializer = ComponentLibraryInitializer()
        library = ComponentLibrary()

        glyph = GlyphContours(unicode=0x6C35, bbox=(0, 0, 6400, 6400))
        c = glyph.add_contour()
        c.add_point(0, 0, True)
        c.add_point(6400, 6400, True)

        result = initializer.add_sample_to_component(library, "nonexistent", glyph)
        assert result is False


class TestUtilityFunctions:
    """测试便捷函数"""

    def test_build_default_component_list(self):
        """测试便捷函数构建默认列表"""
        components = build_default_component_list()
        assert len(components) > 20

    def test_common_radicals_constant(self):
        """测试 COMMON_RADICALS 常量非空"""
        assert len(COMMON_RADICALS) > 50


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流程: 创建 -> 添加样本 -> 统计"""
        initializer = ComponentLibraryInitializer()

        # 1. 创建空部件库
        custom_components = [
            ("part_氵", "三点水", "氵"),
            ("part_木", "木字旁", "木"),
            ("part_口", "口", "口"),
        ]
        library = initializer.build_empty_library(custom_components)
        assert len(library) == 3
        assert library.total_samples() == 0

        # 2. 模拟从字形字典添加样本
        glyph_dict = {
            0x6C35: GlyphContours(unicode=0x6C35, bbox=(0, 0, 6400, 6400), contours=[Contour()]),
            0x6728: GlyphContours(unicode=0x6728, bbox=(0, 0, 6400, 6400), contours=[Contour()]),
        }

        # 手动给轮廓添加点
        for uv in glyph_dict:
            c = glyph_dict[uv].add_contour()
            c.add_point(0, 0, True)
            c.add_point(6400, 6400, True)

        library = initializer.build_from_glyph_dict(glyph_dict, custom_components)

        # 3. 验证结果
        assert len(library) == 3
        # 氵和木应该有样本，口没有
        with_samples = library.components_with_samples()
        assert len(with_samples) == 2

        # 4. 验证摘要
        summary = library.summary()
        assert "3" in summary
        print(f"\n{summary}")
