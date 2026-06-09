"""
测试模块6: HFC 编码器/解码器

Usage:
    pytest tests/test_hfc_encoder.py -v
"""

import sys
import tempfile
from pathlib import Path

import pytest

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hfc.component_library import Component, ComponentLibrary
from hfc.component_matcher import EncodedChar, PartInstance, Transform
from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours
from hfc.hfc_encoder import (
    FLAG_BROTLI,
    HFCEncoder,
    HFC_MAGIC,
    HFC_VERSION,
    EncodeOptions,
    EncodeResult,
    DecodeResult,
    encode_to_file,
    decode_from_file,
)


# 辅助函数
def _make_simple_library() -> ComponentLibrary:
    """构造简单部件库"""
    library = ComponentLibrary()

    comp1 = Component(id="radical_water", name="氵", semantic="氵")
    glyph1 = GlyphContours(unicode=0x6C34, bbox=(0, 0, 6400, 6400))
    c1 = glyph1.add_contour()
    c1.add_point(0, 0, True)
    c1.add_point(3200, 0, True)
    c1.add_point(3200, 6400, True)
    comp1.add_sample_from_glyph(glyph1)
    library.add_component(comp1)

    comp2 = Component(id="radical_wood", name="木", semantic="木")
    glyph2 = GlyphContours(unicode=0x6728, bbox=(0, 0, 6400, 6400))
    c2 = glyph2.add_contour()
    c2.add_point(0, 0, True)
    c2.add_point(6400, 0, True)
    c2.add_point(6400, 6400, True)
    c2.add_point(0, 6400, True)
    comp2.add_sample_from_glyph(glyph2)
    library.add_component(comp2)

    return library


def _make_encoded_chars() -> list[EncodedChar]:
    """构造一些编码字符"""
    chars: list[EncodedChar] = []

    # COMPONENT 模式字 1
    enc1 = EncodedChar(unicode=0x6C49, mode="COMPONENT", match_score=0.95)
    enc1.parts = [
        PartInstance(
            component_id="radical_water",
            transform=Transform(a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0),
            similarity=0.95,
        )
    ]
    chars.append(enc1)

    # COMPONENT 模式字 2 (多部件)
    enc2 = EncodedChar(unicode=0x6797, mode="COMPONENT", match_score=0.88)
    enc2.parts = [
        PartInstance(
            component_id="radical_water",
            transform=Transform(a=0.8, b=0.0, c=0.0, d=0.8, tx=100.0, ty=0.0),
            similarity=0.90,
        ),
        PartInstance(
            component_id="radical_wood",
            transform=Transform(a=0.9, b=0.0, c=0.0, d=0.9, tx=2000.0, ty=100.0),
            similarity=0.85,
        ),
    ]
    chars.append(enc2)

    # RAW 模式字
    enc3 = EncodedChar(unicode=0x6C38, mode="RAW", match_score=0.0)
    glyph = GlyphContours(unicode=0x6C38, bbox=(0, 0, 6400, 6400))
    c = glyph.add_contour()
    c.add_point(100, 100, True)
    c.add_point(6300, 100, True)
    c.add_point(6300, 6300, True)
    c.add_point(100, 6300, True)
    c.add_point(0, 3200, True)
    enc3.raw_contours = glyph.contours
    chars.append(enc3)

    return chars


# ============================================================================
# EncodeOptions 测试
# ============================================================================

class TestEncodeOptions:
    """测试编码选项"""

    def test_defaults(self):
        opts = EncodeOptions()
        assert opts.use_brotli is True
        assert opts.use_zstd is False
        assert opts.brotli_quality == 11
        assert opts.zstd_level == 22
        assert opts.include_component_samples is True

    def test_custom(self):
        opts = EncodeOptions(
            use_brotli=False,
            use_zstd=True,
            include_component_samples=False,
        )
        assert opts.use_brotli is False
        assert opts.use_zstd is True
        assert opts.include_component_samples is False


# ============================================================================
# EncodeResult 测试
# ============================================================================

class TestEncodeResult:
    """测试编码结果"""

    def test_summary(self):
        result = EncodeResult(
            bytes_written=1024,
            component_count=10,
            char_count=100,
            raw_mode_count=20,
            component_mode_count=80,
            compressed_size=1024,
            uncompressed_size=4096,
            compression_method="brotli",
        )
        summary = result.summary()
        assert "10" in summary
        assert "100" in summary
        assert "75.0%" in summary  # (1 - 1024/4096) * 100 = 75%


# ============================================================================
# HFCEncoder 编码测试
# ============================================================================

class TestHFCEncoder:
    """测试 HFC 编码器/解码器"""

    def test_encode_to_bytes(self):
        """测试编码到字节串"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, result = encoder.encode_to_bytes(library, chars)

        assert len(data) > 0
        assert result.component_count == 2
        assert result.char_count == 3

    def test_header_magic(self):
        """测试文件魔数正确"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False),
        )

        # 检查前 4 字节
        assert data[:4] == HFC_MAGIC

        # 检查版本
        assert data[4:8] == (1).to_bytes(4, "big")

    def test_encode_roundtrip_uncompressed(self):
        """测试编解码往返 (未压缩)"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False),
        )
        decoded = encoder.decode_from_bytes(data)

        # 验证部件库
        assert len(decoded.components) == 2
        assert decoded.components.get_component("radical_water") is not None
        assert decoded.components.get_component("radical_wood") is not None

        # 验证字符数
        assert len(decoded.chars) == 3

        # 验证模式
        assert decoded.chars[0].mode == "COMPONENT"
        assert decoded.chars[0].unicode == 0x6C49
        assert len(decoded.chars[0].parts) == 1

        assert decoded.chars[1].mode == "COMPONENT"
        assert len(decoded.chars[1].parts) == 2

        assert decoded.chars[2].mode == "RAW"
        assert decoded.chars[2].raw_contours is not None
        assert len(decoded.chars[2].raw_contours) == 1

    def test_encode_roundtrip_brotli(self):
        """测试编解码往返 (Brotli 压缩)"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, result = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=True),
        )

        decoded = encoder.decode_from_bytes(data)
        assert len(decoded.components) == 2
        assert len(decoded.chars) == 3

    def test_encode_to_file_and_back(self):
        """测试文件级别的编解码"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.hfc"

            library = _make_simple_library()
            chars = _make_encoded_chars()

            result = encode_to_file(library, chars, str(path))

            assert path.exists()
            assert result.char_count == 3

            decoded = decode_from_file(str(path))
            assert len(decoded.chars) == 3
            assert len(decoded.components) == 2

    def test_empty_library_and_chars(self):
        """测试空输入"""
        encoder = HFCEncoder()
        library = ComponentLibrary()
        chars: list[EncodedChar] = []

        data, result = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False),
        )

        assert result.char_count == 0
        assert result.component_count == 0

        decoded = encoder.decode_from_bytes(data)
        assert len(decoded.chars) == 0
        assert len(decoded.components) == 0

    def test_transform_roundtrip(self):
        """测试变换参数精度保留"""
        encoder = HFCEncoder()
        library = _make_simple_library()

        # 构造带精确变换参数的字
        enc = EncodedChar(unicode=0x6C49, mode="COMPONENT", match_score=0.92)
        enc.parts = [
            PartInstance(
                component_id="radical_water",
                transform=Transform(
                    a=1.5, b=-0.2, c=0.2, d=1.5,
                    tx=123.456, ty=789.012,
                ),
                similarity=0.92,
            )
        ]

        data, _ = encoder.encode_to_bytes(
            library, [enc],
            options=EncodeOptions(use_brotli=False),
        )
        decoded = encoder.decode_from_bytes(data)

        # float32 精度约 6-7 位十进制有效数字
        roundtrip = decoded.chars[0]
        t = roundtrip.parts[0].transform
        assert abs(t.a - 1.5) < 1e-5
        assert abs(t.b - (-0.2)) < 1e-5
        assert abs(t.tx - 123.456) < 0.1
        assert abs(t.ty - 789.012) < 0.1

    def test_raw_mode_contours_preserved(self):
        """测试 RAW 模式的轮廓数据被正确保留"""
        encoder = HFCEncoder()
        library = _make_simple_library()

        # 构造带复杂轮廓的 RAW 字
        enc = EncodedChar(unicode=0x6C38, mode="RAW", match_score=0.0)
        glyph = GlyphContours(unicode=0x6C38, bbox=(0, 0, 6400, 6400))
        c1 = glyph.add_contour()
        c1.add_point(100, 100, True)
        c1.add_point(6300, 100, True)
        c1.add_point(6300, 6300, True)
        c1.add_point(100, 6300, True)
        c2 = glyph.add_contour()
        c2.add_point(2000, 2000, False)
        c2.add_point(4400, 2000, False)
        c2.add_point(4400, 4400, True)
        enc.raw_contours = glyph.contours

        data, _ = encoder.encode_to_bytes(
            library, [enc],
            options=EncodeOptions(use_brotli=False),
        )
        decoded = encoder.decode_from_bytes(data)

        denc = decoded.chars[0]
        assert denc.mode == "RAW"
        assert denc.raw_contours is not None
        assert len(denc.raw_contours) == 2
        # 检查点数
        assert len(denc.raw_contours[0].points) == 4
        assert len(denc.raw_contours[1].points) == 3
        # 检查 on-curve 标记
        assert denc.raw_contours[0].points[0].is_on_curve is True
        assert denc.raw_contours[1].points[0].is_on_curve is False

    def test_invalid_magic_rejected(self):
        """测试无效魔数被拒绝"""
        encoder = HFCEncoder()

        with pytest.raises(ValueError, match="无效的 HFC 文件魔数"):
            encoder.decode_from_bytes(b"INVALID\x00\x00\x00\x00" + b"\x00" * 200)

    def test_too_small_file_rejected(self):
        """测试过小的文件被拒绝"""
        encoder = HFCEncoder()

        with pytest.raises(ValueError):
            encoder.decode_from_bytes(b"too small")

    def test_component_samples_roundtrip(self):
        """测试部件轮廓样本的往返"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False, include_component_samples=True),
        )
        decoded = encoder.decode_from_bytes(data)

        # 检查每个部件都有样本
        for comp_id in ["radical_water", "radical_wood"]:
            comp = decoded.components.get_component(comp_id)
            assert comp is not None
            assert comp.num_samples() > 0
            assert len(comp.contour_samples[0]) > 0

    def test_exclude_component_samples(self):
        """测试排除部件样本"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data_full, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False, include_component_samples=True),
        )
        data_trimmed, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False, include_component_samples=False),
        )

        # 排除样本后文件应该更小
        assert len(data_trimmed) < len(data_full)

    def test_version_preserved(self):
        """测试版本号保留"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False),
        )
        decoded = encoder.decode_from_bytes(data)
        assert decoded.version == HFC_VERSION


# ============================================================================
# 压缩算法测试
# ============================================================================

class TestCompression:
    """测试压缩功能"""

    def test_brotli_produces_smaller_output(self):
        """测试 Brotli 压缩后文件更小"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars() * 10  # 放大数据量让压缩更明显

        data_raw, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False),
        )
        data_brotli, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=True),
        )

        # Brotli 压缩后应该更小
        assert len(data_brotli) <= len(data_raw)

    def test_brotli_roundtrip(self):
        """测试 Brotli 压缩后可正确解码"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, _ = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=True),
        )
        decoded = encoder.decode_from_bytes(data)

        assert len(decoded.chars) == 3
        assert len(decoded.components) == 2

    def test_no_compression_roundtrip(self):
        """测试无压缩模式"""
        encoder = HFCEncoder()
        library = _make_simple_library()
        chars = _make_encoded_chars()

        data, result = encoder.encode_to_bytes(
            library, chars,
            options=EncodeOptions(use_brotli=False, use_zstd=False),
        )
        assert result.compression_method == "none"

        decoded = encoder.decode_from_bytes(data)
        assert len(decoded.chars) == 3


# ============================================================================
# 完整流程测试
# ============================================================================

class TestFullPipeline:
    """端到端完整流程"""

    def test_full_pipeline(self):
        """完整流程: 部件库 -> 编码字符 -> 编码到文件 -> 解码回来"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.hfc"

            # 1. 构造部件库
            library = ComponentLibrary()
            for i in range(5):
                comp = Component(
                    id=f"radical_{i:03d}",
                    name=f"部件{i}",
                    semantic=chr(0x4E00 + i),
                )
                glyph = GlyphContours(
                    unicode=0x4E00 + i, bbox=(0, 0, 6400, 6400)
                )
                c = glyph.add_contour()
                for j in range(8):
                    c.add_point(j * 800, j * 800, True)
                comp.add_sample_from_glyph(glyph)
                library.add_component(comp)

            # 2. 构造编码字符 (混合模式)
            chars: list[EncodedChar] = []
            for i in range(10):
                if i % 2 == 0:
                    enc = EncodedChar(
                        unicode=0x6C00 + i,
                        mode="COMPONENT",
                        match_score=0.90 - i * 0.02,
                    )
                    enc.parts = [
                        PartInstance(
                            component_id=f"radical_{i % 5:03d}",
                            transform=Transform.identity(),
                            similarity=0.90 - i * 0.02,
                        )
                    ]
                    chars.append(enc)
                else:
                    enc = EncodedChar(
                        unicode=0x6C00 + i,
                        mode="RAW",
                        match_score=0.0,
                    )
                    glyph = GlyphContours(
                        unicode=0x6C00 + i, bbox=(0, 0, 6400, 6400)
                    )
                    c = glyph.add_contour()
                    c.add_point(100, 100, True)
                    c.add_point(6300, 100, True)
                    c.add_point(6300, 6300, True)
                    enc.raw_contours = glyph.contours
                    chars.append(enc)

            # 3. 编码
            encoder = HFCEncoder()
            result = encoder.encode_to_file(library, chars, str(path))
            summary = result.summary()
            print(f"\n{summary}")

            # 4. 解码
            decoded = decode_from_file(str(path))

            # 5. 验证
            assert len(decoded.components) == 5
            assert len(decoded.chars) == 10

            comp_count = sum(1 for c in decoded.chars if c.mode == "COMPONENT")
            raw_count = sum(1 for c in decoded.chars if c.mode == "RAW")
            assert comp_count == 5
            assert raw_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
