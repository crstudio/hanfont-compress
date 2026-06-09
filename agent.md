# HanFont Compress Agent 工作指南

> 本指南面向正在开发/维护本项目的 AI Agent。遵循本指南以确保开发工作的一致性。

---

## 1. 项目核心目标

构建一个汉字字体专用压缩/解压工具。核心思路：**部件/笔画轮廓复用**。

- **正向**：字体生成工具用"部件 + 变换参数"生成字形
- **逆向**：本工具从已生成字形中提取"部件 + 变换参数"实现压缩

**硬约束**：人工标注字数 ≤ 1000。

---

## 2. 模块开发顺序（严格遵循）

1. **模块1**: 字形轮廓提取器 (`src/hfc/glyph_extractor.py`)
2. **模块5 (解码器部分)**: 简单的轮廓 → 渲染工具 (`src/hfc/renderer.py`)
3. **模块2**: 部件库初始化器 (`src/hfc/component_library.py`)
4. **模块3**: 部件匹配编码器 (`src/hfc/component_matcher.py`)
5. **模块4**: 人工审核工具 (`src/hfc/reviewer.py`)
6. **模块5 (编码器部分)**: 完整 .hfc 序列化/反序列化 (`src/hfc/hfc_codec.py`)

每完成一个模块，需有对应的测试文件在 `tests/` 目录下。

---

## 3. 技术栈约定

| 方面 | 约定 |
|------|------|
| 语言 | Python 3.10+ |
| 核心库 | fontTools, NumPy, SciPy, Pillow, brotli, FastAPI |
| 前端 | Vue 3 |
| 代码风格 | PEP 8，black 格式化，类型注解 (type hints) |
| 测试 | pytest |
| 数据结构 | 严格遵循 `docs/technical-design.md` 第 3 节 |
| 输出格式 | 自定义 .hfc 二进制，参考 `docs/technical-design.md` 第 6 节 |

### 3.1 代码组织

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

### 3.2 配置与参数

- 容差参数集中定义：`MatchConfig` dataclass（参考 tech design 3.4 节）
- 避免在算法代码中写死 magic number，统一通过配置对象传入

---

## 4. 关键算法实现指南

### 4.1 轮廓提取（模块1）

使用 fontTools 的 `TTFont` + `RecordingPen` / `TTGlyphPen`：

```
TTFont(font_path) → 遍历 cmap → 获取 glyph name → 用 pen 提取轮廓点
```

注意：过滤汉字范围（CJK Unified Ideographs: U+4E00..U+9FFF，扩展区按需处理）。

### 4.2 轮廓相似度匹配（模块3）

- 步骤1：**Procrustes 分析**（scipy.spatial.procrustes）将两个点集对齐
- 步骤2：**Hausdorff 距离** 或 **平均最近邻距离** 计算相似度
- 步骤3：若相似度 > `similarity_threshold`（默认 0.85）则接受匹配

输出：仿射变换矩阵 + 差分修正（可选）。

### 4.3 仿射变换拟合

使用 `scipy.linalg.lstsq` 求解最小二乘问题：

```
目标点集 = [a b; c d] * 部件点集 + [tx; ty]
```

约束：`max_scale_deviation ≤ 0.3`, `max_rotation ≤ 5°`。

### 4.4 人工标注约束

`ReviewCounter` 全局单例，严格计数。超过 1000 字 → 自动降级为 `mode='RAW'`。

---

## 5. 验证策略

每个模块完成后必须：

1. **单元测试**：验证核心数据结构和算法
2. **像素级渲染对比**：用 Pillow 将提取的轮廓渲染到位图，与原始字体渲染结果对比（SSIM ≥ 0.99）
3. **还原测试**：编码 → 解码 → 渲染，确保还原误差在容差内

---

## 6. 数据结构变更流程

若需修改核心数据结构（GlyphContours / ComponentLibrary / EncodedChar）：

1. 先在 `docs/technical-design.md` 中更新对应章节
2. 讨论确认后再修改代码
3. 更新相关测试

---

## 7. 提交规范

- 每个模块一个 commit 或 PR
- commit message 格式：`模块N: 简短描述`
- 包含：代码 + 测试 + 简短说明

---

## 8. 常见问题速查

| 问题 | 参考位置 |
|------|---------|
| 数据结构定义 | docs/technical-design.md 第 3 节 |
| 处理流程 | docs/technical-design.md 第 4 节 |
| 算法选择 | docs/technical-design.md 第 5 节 |
| 文件格式 | docs/technical-design.md 第 6 节 |
| 人工标注约束 | docs/technical-design.md 第 7 节 |
| 开发顺序 | docs/technical-design.md 第 8 节 + 本指南第 2 节 |
| 参考开源项目 | docs/technical-design.md 第 10 节 |
