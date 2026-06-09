# HanFont Compress 技术方案文档 v0.1

## 1. 项目概述

汉字字体专用压缩/解压软件。核心思想：**汉字部件复用 + 笔画轮廓复用**。

- 正向：字体生成工具用"部件+变换参数"生成字形
- 逆向：本工具从已生成字形中提取"部件+变换参数"，实现压缩

**核心约束**：人工标注字数 ≤ 1000。

---

## 2. 技术选型

### 2.1 编程语言

| 层级 | 语言 | 原因 |
|------|------|------|
| 主逻辑 | **Python 3.10+** | 快速原型、fontTools 生态完善 |
| 计算密集模块 | **Python + NumPy/SciPy**（前期）→ **Rust/CUDA**（后期优化） | 几何相似度计算可用 NumPy 向量化，后期再 GPU 加速 |
| 数据序列化 | **自定义二进制格式 .hfc** | 我们自己设计 |

### 2.2 核心依赖库

| 库 | 用途 |
|----|------|
| **fontTools** | TTF/OTF 解析、字形轮廓提取、glyf/CFF 统一处理 |
| **NumPy** | 点序列运算、变换矩阵、几何距离计算 |
| **SciPy** | Procrustes 分析、最小二乘拟合 |
| **FastAPI** | 人工审核 Web UI 后端 API |
| **Vue 3** | 人工审核 Web UI 前端（单页应用） |
| **Pillow**（可选）| 字形渲染到位图做视觉验证 |
| **brotli / zstandard** | 最终数据流通用压缩 |

### 2.3 字体格式

| 维度 | 结论 |
|------|------|
| 首选输入格式 | **TTF**（TrueType，二次贝塞尔） |
| OTF 支持 | ✅ 通过 fontTools 自动兼容 |
| 是否需要手动区分格式 | ❌ 不需要，fontTools 透明处理 |

**为什么 TTF 优先**：
- 二次贝塞尔（1 控制点/段）比三次贝塞尔（2 控制点/段）计算量小一半
- glyf table 直接存储点序列，无需解释执行 charstring 字节码
- 中文字体生态中 TTF 占绝对主导（思源黑体/宋体/楷体等）

---

## 3. 核心数据结构

### 3.1 字形轮廓点序列 (GlyphContours)

```python
@dataclass
class Contour:
    points: List[Tuple[int, int, bool]]   # (x, y, is_on_curve)

@dataclass
class GlyphContours:
    unicode: int                           # 字符编码
    contours: List[Contour]                # 轮廓列表
    bbox: Tuple[int, int, int, int]        # (xMin, yMin, xMax, yMax)
```

### 3.2 部件库 (ComponentLibrary)

```python
@dataclass
class Component:
    id: str                                # 部件唯一ID
    name: str                              # 语义名称，如"氵""木"
    contour_sets: List[List[Contour]]      # 多态样本（同一偏旁的不同形态）
    usage_count: int                       # 被多少字引用

@dataclass
class ComponentLibrary:
    components: Dict[str, Component]
```

### 3.3 汉字编码结果 (EncodedChar)

```python
@dataclass
class Transform:
    # 仿射变换矩阵: [a b; c d] * [x; y] + [tx; ty]
    a: float
    b: float
    c: float
    d: float
    tx: float
    ty: float

@dataclass
class PartInstance:
    component_id: str                      # 引用的部件ID
    transform: Transform                   # 仿射变换
    contour_override: Optional[List[Contour]]  # 差分修正（可选）

@dataclass
class EncodedChar:
    unicode: int
    mode: Literal['COMPONENT', 'RAW']      # 部件编码 / 降级为原始轮廓
    parts: List[PartInstance]              # mode='COMPONENT' 时使用
    raw_contours: Optional[List[Contour]]  # mode='RAW' 时使用
```

### 3.4 容差配置

```python
@dataclass
class MatchConfig:
    # 轮廓相似度阈值: 越高越严格, 匹配越少, 人工越多
    similarity_threshold: float = 0.85

    # 最大像素误差（渲染后验证）
    max_pixel_diff: int = 1

    # 仿射变换参数约束
    max_scale_deviation: float = 0.3       # 缩放比例与1.0相差不超过30%
    max_rotation: float = 5.0              # 旋转不超过5度

    # 人工标注硬约束
    max_manual_reviews: int = 1000
```

---

## 4. 代码组织

### 4.1 项目目录结构

```
hanfont_compress/
├── docs/
│   └── technical-design.md   # 技术方案（权威参考）
├── src/
│   ├── hfc/                  # 核心包
│   │   ├── glyph_extractor.py    # 模块1
│   │   ├── renderer.py           # 模块5 (渲染)
│   │   ├── component_library.py  # 模块2
│   │   ├── component_matcher.py  # 模块3
│   │   ├── reviewer.py           # 模块4 (业务逻辑)
│   │   └── hfc_codec.py          # 模块5 (编解码)
│   ├── api/                  # Web UI 后端 (FastAPI)
│   │   └── main.py
│   └── web/                  # Web UI 前端 (Vue3)
│       ├── index.html
│       └── src/
└── tests/                    # 对应每个模块的测试
```

### 4.2 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 模块1 | glyph_extractor.py | 字形轮廓提取器 |
| 模块2 | component_library.py | 部件库初始化器 |
| 模块3 | component_matcher.py | 部件匹配编码器 |
| 模块4 | reviewer.py | 人工审核工具 |
| 模块5 | renderer.py | 字形渲染器 |
| 模块5 | hfc_codec.py | 编码器/解码器 |
| API | api/main.py | Web UI 后端 API |
| Web | web/ | Web UI 前端 (Vue3) |

---

## 5. 处理流程架构

```
输入字体文件 (.ttf/.otf)
    │
    ├─→ [模块1] 字形轮廓提取器 (GlyphExtractor)
    │      fontTools → GlyphContours
    │      - 遍历所有字形（按 unicode 过滤汉字）
    │      - 统一提取轮廓点序列
    │
    ├─→ [模块2] 部件库初始化器 (ComponentLibraryInitializer)
    │      - 加载部首表（Unihan kRSUnicode + GlyphWiki 数据）
    │      - 从样本字中提取每个部首的典型轮廓样本
    │      - 每个偏旁保留 3-5 个多态样本
    │      - 输出: ComponentLibrary
    │
    ├─→ [模块3] 部件匹配编码器 (ComponentMatcher)
    │      对每个汉字:
    │      1. 用部首表的位置信息裁剪候选区域
    │      2. 与部件库做多态样本匹配（Procrustes 分析 + Hausdorff 距离）
    │      3. 最小二乘拟合仿射变换参数
    │      4. 若相似度 > threshold → mode='COMPONENT'
    │         否则 → mode='RAW'，加入待人工审核队列
    │
    ├─→ [模块4] 人工审核工具 (ManualReviewer) — Web UI
    │      FastAPI + Vue3 单页应用，浏览器访问
    │      - 仪表盘：总览匹配成功率、部件库覆盖率、未匹配字数统计
    │      - 未匹配字列表：按相似度排序，显示每个字的:
    │        · 原始渲染图 vs 还原渲染图（并排对比）
    │        · 匹配得分（0~100%）
    │        · 候选部件列表 + 变换参数预览
    │      - 人工操作选项:
    │        · 接受降级（保持 RAW 模式）
    │        · 指定为新部件（加入部件库）
    │        · 手动选择匹配部件 + 微调参数
    │        · 调整容差后重新匹配
    │      - 实时计数：已人工处理 N/1000，硬约束超限则禁止继续
    │      - 导出：审核完成后导出最终 .hfc
    │
    └─→ [模块5] 编码器 + 还原验证器 (Encoder + Validator)
           - 序列化 → 自定义 .hfc 二进制文件
           - 解码器从 .hfc 还原字形
           - 渲染到位图 → SSIM / 像素差异验证
           - 失败 → 自动降级为 RAW 模式
```

---

## 5. 关键算法选型

| 问题 | 算法选择 | 说明 |
|------|---------|------|
| 轮廓相似度匹配 | **Procrustes 分析 + Hausdorff 距离** | 先做形状归一化对齐，再算点集最大最小距离 |
| 仿射变换拟合 | **最小二乘法** (scipy.linalg.lstsq) | 拟合从部件轮廓到目标轮廓的变换矩阵 |
| 部件区域分割 | **启发式边界框分割** | 基于部首位置（左/右/上/下/内/外）做初步裁剪 |
| 像素级还原验证 | **Pillow 渲染 + SSIM** | 还原后渲染到位图与原图比较 |
| 最终数据压缩 | **Brotli** | 对编码后的字节流做二次压缩 |

---

## 6. 输出文件格式 (.hfc)

```
文件结构:
┌────────────────────────────────────────────┐
│ Header (固定长度)                          │
│   magic: "HFC1" (4字节)                    │
│   version: uint16                          │
│   component_count: uint32                  │
│   char_count: uint32                       │
│   component_section_offset: uint32         │
│   char_section_offset: uint32              │
├────────────────────────────────────────────┤
│ Component Library Section                  │
│   (每个部件的轮廓数据，变长)                │
├────────────────────────────────────────────┤
│ Encoded Chars Section                      │
│   (每个汉字的编码结果，变长)                │
├────────────────────────────────────────────┤
│ Footer                                     │
│   checksum: uint32 (CRC32)                 │
└────────────────────────────────────────────┘
```

可选：整个文件尾部再用 Brotli 做一次流式压缩。

---

## 7. 人工标注约束实现

```python
class ReviewCounter:
    """
    硬约束: 人工标注字数 ≤ MAX_MANUAL_REVIEWS (1000)
    超限: 自动降级为 RAW 模式，不再进入人工审核队列
    """
    MAX_MANUAL_REVIEWS = 1000

    def __init__(self):
        self.current = 0
        self.overflow_count = 0   # 超限后被自动降级的字数

    def mark_for_review(self, char: EncodedChar) -> bool:
        if self.current >= self.MAX_MANUAL_REVIEWS:
            char.mode = 'RAW'
            self.overflow_count += 1
            return False
        self.current += 1
        return True
```

---

## 8. 模块开发顺序

1. **模块1**: 字形轮廓提取器（最先开发，验证数据通路）
2. **模块5 (解码器部分)**: 简单的轮廓 → 渲染工具（用于验证）
3. **模块2**: 部件库初始化器（基于部首表数据）
4. **模块3**: 部件匹配编码器（核心算法，最耗时）
5. **模块4**: 人工审核工具（简单命令行或 web UI）
6. **模块5 (编码器部分)**: 完整 .hfc 序列化/反序列化

---

## 9. GPU 加速路线（后期优化）

| 模块 | GPU 化方式 | 预计加速比 |
|------|-----------|-----------|
| 轮廓相似度计算 | CuPy 替换 NumPy 核心循环 | 10-50x |
| 批量字形渲染 | CUDA 光栅化（或保留 CPU） | 5-10x |
| 多字并行匹配 | 批处理 + CUDA 流 | 与 batch size 相关 |

前期不强制 GPU，保持 CPU 可运行。

---

## 10. 参考开源项目与技术

### 10.1 字体处理核心

| 项目 | 许可证 | 用途 |
|------|--------|------|
| **fontTools** (https://github.com/fonttools/fonttools) | MIT | Python 字体处理事实标准。解析 TTF/OTF，提取 glyf/CFF 表，统一轮廓访问 |
| **FontForge** (https://fontforge.org) | GPLv3 | 字体可视化调试工具。命令行模式可用于字体格式转换、轮廓检查 |
| **HarfBuzz** (https://github.com/harfbuzz/harfbuzz) | MIT | 字形渲染/shaping 引擎。可用于验证还原后的字形渲染正确性 |
| **freetype-py** (https://github.com/rougier/freetype-py) | BSD | Freetype 的 Python 绑定。可做精确的像素级渲染对比 |

### 10.2 部件/字形数据源

| 项目 | 用途 | 说明 |
|------|------|------|
| **GlyphWiki** (https://glyphwiki.org) | 汉字部件拆分关系数据 | 已有大量汉字的手工部件拆分记录，可作为初始部件库的种子数据 |
| **Unihan Database** (https://www.unicode.org/charts/unihan.html) | 部首索引、康熙部首位置 | `kRSUnicode` 字段记录每个汉字的康熙部首和位置信息 |
| **IDS (Ideographic Description Sequences)** | 汉字结构描述 | Unicode 中描述汉字如何由部件组合而成，如 "⿰氵每"（海） |

### 10.2.1 正向字体生成工具（逆向参考）

**核心思想**：本项目是这些工具的"逆向"——它们从部件+参数合成字形，我们从字形反推部件+参数。

| 项目 | 许可证 | 原理 | 与本项目的关联 |
|------|--------|------|---------------|
| **MetaFont** (Donald Knuth) | 自由软件 | **纯参数化字体描述**：用 pen 参数（宽度、形状、倾角）+ 字形骨架描述笔画 | 鼻祖级思想借鉴：理解"字形 = 骨架 + 笔画参数"的建模方式 |
| **GlyphWiki** (https://glyphwiki.org) | CC BY-SA | **部件组合引擎**：每个汉字定义为由基础部件（URO 部件）通过位置算子（⿰⿱⿲⿳⿴⿵⿶⿷⿸⿹⿺⿻）组合而成 | **最直接的参考**：部件定义和组合方式可以直接复用；从"部件怎样拼成字"反推"字怎样拆成部件" |
| **FontForge 批量字体脚本** | GPLv3 | **程序化字形生成**：用 Python 脚本批量从部件轮廓生成完整字体 | 作为生成侧参考：理解部件变换（缩放、平移、旋转）后如何正确拼接为完整字形 |
| **METAFONT 中文项目** (如 cwTeX, Chinese METAFONT) | 自由软件 | **笔画参数化**：将汉字分解为横、竖、撇、捺等基础笔画，通过参数控制宽度、弧度、起收笔 | 笔画级复用的参考实现，理解"笔画 = 参数化曲线"的建模 |
| **CJK Type Foundry** (https://github.com/cjkvi) 相关工具 | 多种 | **CJK 字符集字形的批量生成与合成工具集** | 中文语境下的字体工程实践 |
| **SVG Font / SVG 字形合成** | - | **用 SVG 路径描述部件，通过 SVG transform 组合** | 轻量级的部件变换与拼接参考，适合原型阶段验证部件+变换的还原效果 |

**关键要点**：
- GlyphWiki 不仅是数据源，它本身就是"部件→字形"的正向生成系统，其部件定义和组合规则最值得深入研究
- MetaFont 的"骨架 + 笔画参数"思想是我们"部件 + 变换"建模的理论源头
- 理解"正向生成怎样拼接"对设计"逆向拆解怎样分割"有直接启发

### 10.3 几何/形状匹配算法

| 技术 / 论文 | 用途 | 关键思想 |
|-------------|------|---------|
| **Procrustes Analysis** | 轮廓形状归一化对齐 | 通过平移/缩放/旋转将两个点集对齐到最佳匹配位置 |
| **Hausdorff Distance** | 轮廓相似度度量 | 两个点集之间的最大最小距离，衡量形状差异 |
| **Frechet Distance** | 曲线相似度 | 衡量两条曲线之间的相似性，比 Hausdorff 更精确但计算更慢 |
| **ICP (Iterative Closest Point)** | 点集精细配准 | 迭代寻找最优变换，可用于 Procrustes 后的精调 |
| **Douglas-Peucker 算法** | 轮廓点简化 | 减少轮廓点数，加速匹配计算 |

### 10.4 图像/渲染验证

| 项目 | 用途 |
|------|------|
| **Pillow** (https://pillow.readthedocs.io) | 字形渲染到位图，用于像素级对比 |
| **scikit-image** (https://scikit-image.org) | SSIM 结构相似性指标计算 |
| **OpenCV** (https://opencv.org) | 模板匹配、轮廓对比（可选） |

### 10.5 汉字字体相关研究项目

| 项目 | 说明 |
|------|------|
| **思源字体 (Source Han)** | Adobe/Google 开源中日韩字体，作为测试素材 |
| **TW-Kai / TW-Sung** | 开源的中文楷体/宋体字体 |
| **MetaFont (Knuth)** | 参数化字体生成思想的鼻祖，可借鉴参数化思想 |

### 10.6 压缩/序列化

| 项目 | 用途 |
|------|------|
| **Brotli** (https://github.com/google/brotli) | 通用压缩，比 gzip 更优 |
| **Zstandard** (https://facebook.github.io/zstd) | 高压缩率 + 高速度的通用压缩 |
| **MessagePack** (https://msgpack.org) | 紧凑的二进制序列化（可选，作为内部数据格式参考） |
| **FlatBuffers** / **Cap'n Proto** | 零拷贝序列化（后期若追求极致性能可考虑） |

### 10.7 其他参考资源

| 资源 | 说明 |
|------|------|
| **OpenType Specification** (https://learn.microsoft.com/en-us/typography/opentype/spec/) | TTF/OTF 格式官方规范 |
| **TrueType 参考手册** | 理解 glyf 表、hmtx 表的内部结构 |
| **Unicode 字符数据库 (UCD)** | 汉字范围、属性判定 |
| **"Digital Typography" (Donald Knuth)** | 参数化字体生成的经典著作 |

