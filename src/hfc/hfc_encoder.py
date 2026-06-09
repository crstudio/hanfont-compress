"""
模块6: 压缩/序列化输出 (HFCEncoder)

将部件库和编码后的汉字数据序列化为 .hfc 文件格式，并支持
Brotli / zstandard 通用压缩收尾。

文件格式 (HFC v1):
---------------------------------------------------------------
+-----------------------------------+
| Header (12 bytes)                |
|   magic: "HFC\x01"   (4 bytes)   |
|   version: 1         (uint32)    |
|   flags: bit0=Brotli, bit1=zstd  |
|   char_count: uint32             |
+-----------------------------------+
| Component Library Section         |
|   comp_count: uint32             |
|   [每个部件]:                    |
|     id_len: uint16               |
|     id: bytes (UTF-8)            |
|     name_len: uint16             |
|     name: bytes (UTF-8)          |
|     semantic_len: uint16         |
|     semantic: bytes (UTF-8)      |
|     contour_sample_count: uint32|
|     [每个样本]:                  |
|       contour_count: uint32      |
|       [每个轮廓]:                |
|         point_count: uint32      |
|         [每个点]:                |
|           x: int32 (F26Dot6)     |
|           y: int32 (F26Dot6)     |
|           flags: uint8           |
|             bit0 = is_on_curve   |
+-----------------------------------+
| Character Encoding Section        |
|   encoded_char_count: uint32     |
|   [每个字]:                      |
|     unicode: uint32              |
|     mode: uint8                  |
|       0 = COMPONENT             |
|       1 = RAW                    |
|     [COMPONENT mode]:            |
|       part_count: uint32         |
|       [每个部件实例]:            |
|         comp_id_len: uint16      |
|         comp_id: bytes           |
|         transform: 6 x float32   |
|           (a, b, tx, c, d, ty)  |
|         similarity: float32      |
|     [RAW mode]:                  |
|       bbox: 4 x int32            |
|         (xMin, yMin, xMax, yMax)|
|       contour_count: uint32      |
|       [每个轮廓]:                |
|         point_count: uint32      |
|         [每个点]:                |
|           x: int32, y: int32     |
|           flags: uint8           |
+-----------------------------------+
| Optional: Brotli/Zstd 压缩流    |
+-----------------------------------+

参考: docs/technical-design.md 第 3.5 节
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Optional

from .component_library import Component, ComponentLibrary
from .component_matcher import EncodedChar, PartInstance, Transform
from .glyph_extractor import Contour, ContourPoint, GlyphContours


# 文件魔数: HFC\x01
HFC_MAGIC = b"HFC\x01"
HFC_VERSION = 1

# flag 位
FLAG_BROTLI = 0x01
FLAG_ZSTD = 0x02
FLAG_NONE = 0x00

# 模式常量
MODE_COMPONENT = 0
MODE_RAW = 1

# struct 格式
HEADER_FORMAT = "!4sIII"   # magic(4s) + version(I) + flags(I) + char_count(I)
UINT16_FORMAT = "!H"
UINT32_FORMAT = "!I"
INT32_FORMAT = "!i"


@dataclass
class EncodeOptions:
    """编码选项"""

    use_brotli: bool = True
    use_zstd: bool = False
    brotli_quality: int = 11  # 0-11
    zstd_level: int = 22  # 1-22
    include_component_samples: bool = True


@dataclass
class EncodeResult:
    """编码结果"""

    bytes_written: int = 0
    component_count: int = 0
    char_count: int = 0
    raw_mode_count: int = 0
    component_mode_count: int = 0
    compressed_size: int = 0
    uncompressed_size: int = 0
    compression_method: str = "none"

    def summary(self) -> str:
        ratio = 0.0
        if self.uncompressed_size > 0:
            ratio = (1 - self.compressed_size / self.uncompressed_size) * 100
        lines = [
            "=== HFC 编码报告 ===",
            f"部件库大小: {self.component_count} 个部件",
            f"字符总数: {self.char_count}",
            f"  部件编码模式: {self.component_mode_count}",
            f"  原始轮廓模式: {self.raw_mode_count}",
            f"压缩方法: {self.compression_method}",
            f"压缩前: {self.uncompressed_size:,} bytes",
            f"压缩后: {self.compressed_size:,} bytes",
            f"压缩率: {ratio:.1f}%",
        ]
        return "\n".join(lines)


@dataclass
class DecodeResult:
    """解码结果"""

    components: ComponentLibrary
    chars: list[EncodedChar]
    bytes_read: int = 0
    version: int = HFC_VERSION


class HFCEncoder:
    """
    HFC 文件编码器/解码器。

    编码示例:
        encoder = HFCEncoder()
        result = encoder.encode_to_file(
            component_library,
            encoded_chars,
            "output.hfc",
        )
        print(result.summary())

    解码示例:
        decoder = HFCEncoder()
        result = decoder.decode_from_file("output.hfc")
        print(f"解码了 {len(result.chars)} 个字符, "
              f"{len(result.components)} 个部件")
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # 主编码接口
    # ------------------------------------------------------------------

    def encode_to_file(
        self,
        components: ComponentLibrary,
        encoded_chars: list[EncodedChar],
        output_path: str | Path,
        options: Optional[EncodeOptions] = None,
    ) -> EncodeResult:
        """
        编码到文件。

        Args:
            components: 部件库
            encoded_chars: 已编码的字符列表
            output_path: 输出文件路径
            options: 编码选项

        Returns:
            EncodeResult
        """
        if options is None:
            options = EncodeOptions()

        # 1. 先在内存中构建未压缩的 body 字节流 (不含 header)
        body_bytes = self._build_body_bytes(
            components, encoded_chars, options
        )

        # 2. 决定 flags
        flags = FLAG_NONE
        compressed_body = body_bytes

        if options.use_brotli:
            try:
                import brotli
                compressed_body = brotli.compress(
                    body_bytes, quality=options.brotli_quality
                )
                flags = FLAG_BROTLI
            except ImportError:
                pass
        elif options.use_zstd:
            try:
                import zstandard as zstd
                cctx = zstd.ZstdCompressor(level=options.zstd_level)
                compressed_body = cctx.compress(body_bytes)
                flags = FLAG_ZSTD
            except ImportError:
                pass

        # 3. 写 header (未压缩, 16 bytes)
        header = struct.pack(
            HEADER_FORMAT,
            HFC_MAGIC, HFC_VERSION, flags, len(encoded_chars),
        )

        # 4. 最终文件 = header + body
        final_bytes = header + compressed_body
        raw_total = len(header) + len(body_bytes)

        # 5. 写入文件
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(final_bytes)

        # 6. 构造结果
        result = self._make_result(
            components, encoded_chars, raw_total,
            len(final_bytes), options
        )
        return result

    def encode_to_bytes(
        self,
        components: ComponentLibrary,
        encoded_chars: list[EncodedChar],
        options: Optional[EncodeOptions] = None,
    ) -> tuple[bytes, EncodeResult]:
        """
        编码到内存字节串。

        Returns:
            (bytes, EncodeResult)
        """
        if options is None:
            options = EncodeOptions()

        body_bytes = self._build_body_bytes(
            components, encoded_chars, options
        )

        flags = FLAG_NONE
        compressed_body = body_bytes

        if options.use_brotli:
            try:
                import brotli
                compressed_body = brotli.compress(
                    body_bytes, quality=options.brotli_quality
                )
                flags = FLAG_BROTLI
            except ImportError:
                pass
        elif options.use_zstd:
            try:
                import zstandard as zstd
                cctx = zstd.ZstdCompressor(level=options.zstd_level)
                compressed_body = cctx.compress(body_bytes)
                flags = FLAG_ZSTD
            except ImportError:
                pass

        header = struct.pack(
            HEADER_FORMAT,
            HFC_MAGIC, HFC_VERSION, flags, len(encoded_chars),
        )
        final_bytes = header + compressed_body
        raw_total = len(header) + len(body_bytes)

        result = self._make_result(
            components, encoded_chars, raw_total,
            len(final_bytes), options
        )
        return final_bytes, result

    # ------------------------------------------------------------------
    # 主解码接口
    # ------------------------------------------------------------------

    def decode_from_file(
        self, input_path: str | Path
    ) -> DecodeResult:
        """
        从文件解码。

        Args:
            input_path: .hfc 文件路径

        Returns:
            DecodeResult
        """
        with open(input_path, "rb") as f:
            data = f.read()

        return self.decode_from_bytes(data)

    def decode_from_bytes(self, data: bytes) -> DecodeResult:
        """
        从字节串解码。

        Args:
            data: 完整的 .hfc 文件字节

        Returns:
            DecodeResult
        """
        header_size = struct.calcsize(HEADER_FORMAT)
        if len(data) < header_size:
            raise ValueError("HFC 文件太小，无法读取 header")

        # 1. 读取 header (保持未压缩)
        magic, version, flags, _ = struct.unpack_from(HEADER_FORMAT, data, 0)

        if magic != HFC_MAGIC:
            raise ValueError(
                f"无效的 HFC 文件魔数: {magic!r}, 期望 {HFC_MAGIC!r}"
            )

        if version != HFC_VERSION:
            raise ValueError(
                f"不支持的 HFC 版本: {version}, 期望 {HFC_VERSION}"
            )

        # 2. body 部分可能被压缩
        body = data[header_size:]

        if flags & FLAG_BROTLI:
            try:
                import brotli
                body = brotli.decompress(body)
            except ImportError:
                raise RuntimeError(
                    "需要 brotli 库才能解压缩此文件: pip install brotli"
                )
        elif flags & FLAG_ZSTD:
            try:
                import zstandard as zstd
                dctx = zstd.ZstdDecompressor()
                body = dctx.decompress(body)
            except ImportError:
                raise RuntimeError(
                    "需要 zstandard 库才能解压缩此文件: pip install zstandard"
                )
        # flags == FLAG_NONE: body 已是原始字节

        # 3. 解析 body
        return self._parse_body_bytes(body, version)

    # ------------------------------------------------------------------
    # 内部: 编码
    # ------------------------------------------------------------------

    def _build_body_bytes(
        self,
        components: ComponentLibrary,
        encoded_chars: list[EncodedChar],
        options: EncodeOptions,
    ) -> bytes:
        """构建 body 字节流 (不含 header)"""
        buf = bytearray()

        # 写部件库部分
        comp_count = len(components)
        buf.extend(struct.pack(UINT32_FORMAT, comp_count))
        for comp in components:
            self._write_component(buf, comp, options)

        # 写字符编码部分
        char_count = len(encoded_chars)
        buf.extend(struct.pack(UINT32_FORMAT, char_count))
        for enc in encoded_chars:
            self._write_encoded_char(buf, enc)

        return bytes(buf)

    def _write_component(
        self,
        buf: bytearray,
        component: Component,
        options: EncodeOptions,
    ) -> None:
        """写一个部件到缓冲区"""
        # id
        id_bytes = component.id.encode("utf-8")
        buf.extend(struct.pack(UINT16_FORMAT, len(id_bytes)))
        buf.extend(id_bytes)

        # name
        name_bytes = component.name.encode("utf-8") if component.name else b""
        buf.extend(struct.pack(UINT16_FORMAT, len(name_bytes)))
        buf.extend(name_bytes)

        # semantic
        sem_bytes = component.semantic.encode("utf-8") if component.semantic else b""
        buf.extend(struct.pack(UINT16_FORMAT, len(sem_bytes)))
        buf.extend(sem_bytes)

        # 轮廓样本数量
        sample_count = len(component.contour_samples) if options.include_component_samples else 0
        buf.extend(struct.pack(UINT32_FORMAT, sample_count))

        # 写每个样本
        if options.include_component_samples:
            for sample in component.contour_samples:
                contour_count = len(sample)
                buf.extend(struct.pack(UINT32_FORMAT, contour_count))
                for contour in sample:
                    self._write_contour(buf, contour)

    def _write_contour(self, buf: bytearray, contour: Contour) -> None:
        """写一个轮廓到缓冲区"""
        point_count = len(contour.points)
        buf.extend(struct.pack(UINT32_FORMAT, point_count))
        for pt in contour.points:
            # x, y 用 int32, flags 用 uint8
            buf.extend(struct.pack(INT32_FORMAT, pt.x))
            buf.extend(struct.pack(INT32_FORMAT, pt.y))
            flag = 1 if pt.is_on_curve else 0
            buf.append(flag)

    def _write_encoded_char(self, buf: bytearray, enc: EncodedChar) -> None:
        """写一个编码后的字符"""
        # unicode
        buf.extend(struct.pack(UINT32_FORMAT, enc.unicode))

        # mode
        if enc.mode == "COMPONENT":
            buf.extend(struct.pack("!B", MODE_COMPONENT))
            # part instances
            part_count = len(enc.parts)
            buf.extend(struct.pack(UINT32_FORMAT, part_count))
            for part in enc.parts:
                self._write_part_instance(buf, part)
        else:  # RAW
            buf.extend(struct.pack("!B", MODE_RAW))
            # bbox (4 x int32)
            if hasattr(enc, "raw_contours") and enc.raw_contours:
                # 从轮廓计算 bbox
                all_x: list[int] = []
                all_y: list[int] = []
                for contour in enc.raw_contours:
                    for pt in contour:
                        all_x.append(pt.x)
                        all_y.append(pt.y)
                if all_x and all_y:
                    bbox = (min(all_x), min(all_y), max(all_x), max(all_y))
                else:
                    bbox = (0, 0, 0, 0)
            else:
                bbox = (0, 0, 0, 0)
            buf.extend(struct.pack("!iiii", *bbox))

            # 轮廓
            contours = enc.raw_contours if enc.raw_contours else []
            contour_count = len(contours)
            buf.extend(struct.pack(UINT32_FORMAT, contour_count))
            for contour in contours:
                self._write_contour(buf, contour)

    def _write_part_instance(self, buf: bytearray, part: PartInstance) -> None:
        """写一个部件实例"""
        # component_id
        cid_bytes = part.component_id.encode("utf-8")
        buf.extend(struct.pack(UINT16_FORMAT, len(cid_bytes)))
        buf.extend(cid_bytes)

        # transform (a, b, tx, c, d, ty) - 6 个 float32
        buf.extend(struct.pack(
            "!ffffff",
            float(part.transform.a),
            float(part.transform.b),
            float(part.transform.tx),
            float(part.transform.c),
            float(part.transform.d),
            float(part.transform.ty),
        ))

        # similarity float32
        buf.extend(struct.pack("!f", float(part.similarity)))

    # ------------------------------------------------------------------
    # 内部: 结果辅助
    # ------------------------------------------------------------------

    def _make_result(
        self,
        components: ComponentLibrary,
        encoded_chars: list[EncodedChar],
        raw_size: int,
        final_size: int,
        options: EncodeOptions,
    ) -> EncodeResult:
        """构造编码结果统计"""
        comp_mode = sum(1 for e in encoded_chars if e.mode == "COMPONENT")
        raw_mode = sum(1 for e in encoded_chars if e.mode == "RAW")

        if options.use_brotli and final_size != raw_size:
            method = "brotli"
        elif options.use_zstd and final_size != raw_size:
            method = "zstd"
        else:
            method = "none"

        return EncodeResult(
            bytes_written=final_size,
            component_count=len(components),
            char_count=len(encoded_chars),
            raw_mode_count=raw_mode,
            component_mode_count=comp_mode,
            compressed_size=final_size,
            uncompressed_size=raw_size,
            compression_method=method,
        )

    def _parse_body_bytes(self, data: bytes, version: int) -> DecodeResult:
        """解析 body 字节流"""
        offset = 0
        components = ComponentLibrary()
        chars: list[EncodedChar] = []

        # 1. 解析部件库
        (comp_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
        offset += struct.calcsize(UINT32_FORMAT)

        for _ in range(comp_count):
            comp, offset = self._read_component(data, offset)
            components.add_component(comp)

        # 2. 解析字符编码
        (char_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
        offset += struct.calcsize(UINT32_FORMAT)

        for _ in range(char_count):
            enc, offset = self._read_encoded_char(data, offset)
            chars.append(enc)

        return DecodeResult(
            components=components,
            chars=chars,
            bytes_read=len(data),
            version=version,
        )

    def _read_component(self, data: bytes, offset: int) -> tuple[Component, int]:
        """读取一个部件"""
        # id
        (id_len,) = struct.unpack_from(UINT16_FORMAT, data, offset)
        offset += 2
        comp_id = data[offset:offset + id_len].decode("utf-8")
        offset += id_len

        # name
        (name_len,) = struct.unpack_from(UINT16_FORMAT, data, offset)
        offset += 2
        name = data[offset:offset + name_len].decode("utf-8") if name_len else ""
        offset += name_len

        # semantic
        (sem_len,) = struct.unpack_from(UINT16_FORMAT, data, offset)
        offset += 2
        semantic = data[offset:offset + sem_len].decode("utf-8") if sem_len else ""
        offset += sem_len

        # 轮廓样本
        (sample_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
        offset += 4

        comp = Component(id=comp_id, name=name, semantic=semantic)

        for _ in range(sample_count):
            (contour_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
            offset += 4
            sample = []
            for _ in range(contour_count):
                contour, offset = self._read_contour(data, offset)
                sample.append(contour)
            comp.contour_samples.append(sample)

        return comp, offset

    def _read_contour(self, data: bytes, offset: int) -> tuple[Contour, int]:
        """读取一个轮廓"""
        (point_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
        offset += 4

        contour = Contour()
        for _ in range(point_count):
            (x,) = struct.unpack_from(INT32_FORMAT, data, offset)
            offset += 4
            (y,) = struct.unpack_from(INT32_FORMAT, data, offset)
            offset += 4
            flags = data[offset]
            offset += 1
            is_on = bool(flags & 1)
            contour.add_point(x, y, is_on)

        return contour, offset

    def _read_encoded_char(
        self, data: bytes, offset: int
    ) -> tuple[EncodedChar, int]:
        """读取一个编码字符"""
        (unicode_val,) = struct.unpack_from(UINT32_FORMAT, data, offset)
        offset += 4

        (mode,) = struct.unpack_from("!B", data, offset)
        offset += 1

        if mode == MODE_COMPONENT:
            enc = EncodedChar(unicode=unicode_val, mode="COMPONENT")
            (part_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
            offset += 4

            parts: list[PartInstance] = []
            total_sim = 0.0
            for _ in range(part_count):
                part, offset = self._read_part_instance(data, offset)
                parts.append(part)
                total_sim += part.similarity

            enc.parts = parts
            enc.match_score = total_sim / max(part_count, 1)
            return enc, offset

        else:  # RAW
            enc = EncodedChar(unicode=unicode_val, mode="RAW")
            # bbox
            bbox = struct.unpack_from("!iiii", data, offset)
            offset += 16

            # 轮廓
            (contour_count,) = struct.unpack_from(UINT32_FORMAT, data, offset)
            offset += 4

            contours: list[Contour] = []
            for _ in range(contour_count):
                contour, offset = self._read_contour(data, offset)
                contours.append(contour)

            enc.raw_contours = contours
            return enc, offset

    def _read_part_instance(
        self, data: bytes, offset: int
    ) -> tuple[PartInstance, int]:
        """读取一个部件实例"""
        (cid_len,) = struct.unpack_from(UINT16_FORMAT, data, offset)
        offset += 2
        cid = data[offset:offset + cid_len].decode("utf-8")
        offset += cid_len

        # transform
        a, b, tx, c, d, ty = struct.unpack_from("!ffffff", data, offset)
        offset += struct.calcsize("!ffffff")
        transform = Transform(a=a, b=b, c=c, d=d, tx=tx, ty=ty)

        # similarity
        (sim,) = struct.unpack_from("!f", data, offset)
        offset += 4

        return PartInstance(
            component_id=cid,
            transform=transform,
            similarity=float(sim),
        ), offset



# ----------------------------------------------------------------------
# 便捷函数
# ----------------------------------------------------------------------

def encode_to_file(
    components: ComponentLibrary,
    encoded_chars: list[EncodedChar],
    output_path: str | Path,
) -> EncodeResult:
    """便捷函数: 使用默认选项编码到文件"""
    encoder = HFCEncoder()
    return encoder.encode_to_file(components, encoded_chars, output_path)


def decode_from_file(input_path: str | Path) -> DecodeResult:
    """便捷函数: 从文件解码"""
    encoder = HFCEncoder()
    return encoder.decode_from_file(input_path)
