"""
路线C: TrueType Composite Glyph 标准字体内复用。

本文件是路线C的正式入口。
实际实现在同目录的 composite_encoder.py 中。

使用示例:
    from hfc.route_c import RouteCConfig, RouteCEncoder, RouteCResult

参考: docs/try.md 第 6.16-6.22 节（路线 C）
"""

from .composite_encoder import (
    RouteCConfig,
    RouteCEncoder,
    RouteCResult,
    ComponentInfo,
    CompositeEncoder,  # 向后兼容别名
)

__all__ = [
    "RouteCConfig",
    "RouteCEncoder",
    "RouteCResult",
    "ComponentInfo",
    "CompositeEncoder",
]
