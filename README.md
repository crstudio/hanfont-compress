# HanFont Compress

> 汉字字体专用压缩/解压工具。核心思路：**部件/笔画轮廓复用**。

---

## 项目简介

现有字体压缩方案（子集化、通用压缩）无法解决"中文字符集庞大导致字体文件体积巨大"的根本问题。

HanFont Compress 采用 **逆向工程思路**：
- **正向**：字体生成工具用"部件 + 变换参数"合成字形
- **逆向**：本工具从已生成字形中提取"部件 + 变换参数"，将汉字编码为部件引用 + 变换矩阵

从而实现远超通用压缩算法的压缩率。

**硬约束**：人工标注字数 ≤ 1000。

---

## 当前状态

🚧 **开发中**

当前阶段：技术方案已确定，即将开始模块1开发。

---

## 技术选型

| 方面 | 选择 |
|------|------|
| 语言 | Python 3.10+ |
| 核心库 | fontTools, NumPy, SciPy, Pillow, brotli, FastAPI |
| 前端 | Vue 3 |
| 输入格式 | TTF（OTF 通过 fontTools 兼容） |
| 输出格式 | 自定义 .hfc 二进制 |
| 匹配算法 | Procrustes 分析 + Hausdorff 距离 |
| 验证方式 | 渲染到位图 + SSIM 对比 |

完整技术方案见 [docs/technical-design.md](docs/technical-design.md)。

---

## 核心架构

```
输入字体 (.ttf)
    │
    ├─→ [模块1] 字形轮廓提取器
    │    → GlyphContours (轮廓点序列)
    │
    ├─→ [模块2] 部件库初始化器
    │    → ComponentLibrary (部件轮廓样本库)
    │
    ├─→ [模块3] 部件匹配编码器
    │    对每个汉字: 部件匹配 → 计算变换 → 编码为 EncodedChar
    │
    ├─→ [模块4] 人工审核工具
    │    匹配失败的字形 → 人工审核或自动降级
    │
    └─→ [模块5] 编码器 + 还原验证器
         → 自定义 .hfc 文件
         ← 解码器从 .hfc 还原字形
```

详细模块设计见 [docs/technical-design.md 第 4 节](docs/technical-design.md#4-处理流程架构)。

---

## 模块开发顺序

| 顺序 | 模块 | 状态 | 文件 |
|------|------|------|------|
| 1 | 字形轮廓提取器 | 待开发 | `src/hfc/glyph_extractor.py` |
| 2 | 轮廓渲染工具 | 待开发 | `src/hfc/renderer.py` |
| 3 | 部件库初始化器 | 待开发 | `src/hfc/component_library.py` |
| 4 | 部件匹配编码器 | 待开发 | `src/hfc/component_matcher.py` |
| 5 | 人工审核工具 | 待开发 | `src/hfc/reviewer.py` |
| 6 | .hfc 编解码器 | 待开发 | `src/hfc/hfc_codec.py` |

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/technical-design.md](docs/technical-design.md) | 完整技术方案（数据结构、流程、算法、参考项目）|
| [agent.md](agent.md) | Agent 开发工作指南（开发流程、约束、速查）|

---

## 开发者指南

### 环境准备

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install fonttools numpy scipy pillow brotli
pip install pytest
```

### 代码风格

- PEP 8 兼容，使用 black 格式化
- 完整类型注解 (type hints)
- 测试框架：pytest

---

## 许可证

MIT License
