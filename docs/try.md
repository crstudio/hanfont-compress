# 汉字部件复用字体压缩技术方案

## 1. 项目目标

本项目目标是设计一种面向中文字体的结构化压缩算法，通过自动识别汉字中的可复用部件，将原始 TTF/OTF 字体中的重复字形轮廓压缩为：

```text
Component Dictionary + Glyph Recipe + Transform + Optional Delta
```

最终实现：

```text
原始 TTF/OTF
  ↓
部件识别与匹配
  ↓
自定义字体压缩包
  ↓
WASM/JS 解码
  ↓
重建 TTF/OTF/WOFF2
  ↓
浏览器 FontFace 渲染
  ↓
WebUI 查看压缩效果、还原效果、差异效果
```

本方案不把普通 WOFF2 子集化作为主路线，只作为最终对比指标。核心实验集中在汉字部件复用压缩本身。

---

# 2. 核心表达模型

基础模型：

```text
Glyph = Σ(Component_i × Transform_i) + Delta
```

其中：

```text
Component_i：可复用的汉字部件矢量轮廓
Transform_i：该部件在当前汉字中的几何变换
Delta：可选差分修正，用于近无损或无损还原
```

每个汉字不再完整保存所有轮廓，而是尽量保存为：

```text
汉字 = 部件编号 + 位置 + 缩放 + 横纵变形 + 可选旋转 + 可选差分
```

---

# 3. 总体架构

## 3.1 编码端

```text
输入字体 TTF/OTF
  ↓
解析 cmap，筛选汉字 glyph
  ↓
提取 glyph 矢量轮廓
  ↓
生成候选部件
  ↓
部件归一化
  ↓
栅格化生成匹配图
  ↓
部件匹配与去重
  ↓
建立 Component Dictionary
  ↓
生成 Glyph Recipe
  ↓
可选生成 Delta Correction
  ↓
输出自定义压缩包
```

---

## 3.2 解码端

```text
读取自定义压缩包
  ↓
读取 Component Dictionary
  ↓
读取 Glyph Recipe
  ↓
应用 Transform
  ↓
应用 Delta，可选
  ↓
重建 glyph outline
  ↓
生成 TTF/OTF/WOFF2
  ↓
浏览器 FontFace 加载
```

---

## 3.3 WebUI 效果查看端

每条路线都需要配套 WebUI，用于查看压缩效果和字形还原效果。

WebUI 目标：

```text
1. 上传字体文件
2. 选择字符集
3. 选择压缩路线
4. 调整匹配参数
5. 执行压缩
6. 查看部件匹配结果
7. 查看单字重建结果
8. 查看原字形与重建字形差异
9. 查看压缩率、匹配率、错误率、耗时
10. 导出压缩包、重建字体、测试报告
```

---

# 4. 核心数据结构

## 4.1 Component

```ts
interface Component {
  id: number;
  contours: VectorContour[];
  bbox: BBox;
  normalizedWidth: number;
  normalizedHeight: number;
  hash: string;
  complexity: number;
}
```

说明：

```text
id：部件编号
contours：矢量轮廓
bbox：部件包围盒
hash：用于快速匹配的形状哈希
complexity：复杂度，例如点数、轮廓数、面积
```

---

## 4.2 GlyphRecipe

```ts
interface GlyphRecipe {
  unicode: number;
  glyphId: number;
  advanceWidth: number;
  leftSideBearing: number;
  components: GlyphComponentRef[];
  rawContours?: VectorContour[];
}
```

说明：

```text
unicode：字符编码
glyphId：原字体 glyph ID
advanceWidth：字宽
leftSideBearing：左边距
components：部件引用数组
rawContours：无法复用时保存原始轮廓
```

---

## 4.3 GlyphComponentRef

```ts
interface GlyphComponentRef {
  componentId: number;
  transform: Transform2D;
  matchScore: number;
  deltaId?: number;
}
```

---

## 4.4 Transform2D

建议使用 6 参数二维仿射矩阵：

```ts
interface Transform2D {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}
```

对应：

```text
x' = a * x + c * y + e
y' = b * x + d * y + f
```

可表达：

```text
平移
等比缩放
非等比缩放
横向压缩
纵向拉伸
轻微旋转
轻微斜切
```

第一版也可以先用简化结构：

```ts
interface SimpleTransform {
  x: number;
  y: number;
  scaleX: number;
  scaleY: number;
  rotation?: number;
}
```

---

## 4.5 DeltaCorrection

```ts
interface DeltaCorrection {
  id: number;
  mode: 'point-offset' | 'extra-contour' | 'replace-contour';
  data: unknown;
}
```

Delta 用于修正相似匹配后的剩余差异。

---

# 5. 部件匹配方法

## 5.1 候选部件生成

推荐方式：

```text
字体矢量轮廓负责最终保存
栅格化图片只负责相似度匹配
```

流程：

```text
glyph outline
  ↓
contour 分组
  ↓
根据 bbox、距离、相交关系生成候选部件
  ↓
对候选部件进行归一化
  ↓
栅格化为 bitmap
  ↓
用于匹配评分
```

---

## 5.2 匹配评分

不建议只看图片重叠率，推荐组合评分：

```text
Score =
  0.70 × BitmapIoU
+ 0.20 × BBoxSimilarity
+ 0.10 × AreaSimilarity
```

高级版本可以加入：

```text
VectorContourSimilarity
SkeletonSimilarity
StrokeDensitySimilarity
```

---

## 5.3 匹配阈值

建议按部件大小和复杂度动态设置。

```text
大部件：0.95
中部件：0.92
小部件：0.88 ~ 0.90
简单部件：0.95 以上
复杂部件：0.90 ~ 0.94
```

简单部件容易误匹配，例如：

```text
一、二、十、土、工、干、王、口、日、目
```

这类部件需要更高阈值。

---

## 5.4 几何变换支持

建议分级开启。

```text
Level 0：平移
Level 1：平移 + 等比缩放
Level 2：平移 + scaleX + scaleY
Level 3：平移 + scaleX + scaleY + 小角度旋转
Level 4：完整仿射变换 + Delta
```

旋转建议限制：

```text
-3° ~ +3°
```

不要默认允许大角度旋转，否则误匹配风险较高。

---

# 6. 三种实验路线

---

# 路线 A：无 Delta 的部件复用有损压缩

## 6.1 目标

快速验证“汉字部件复用”是否具备实际压缩潜力。

## 6.2 特点

```text
只保存：
Component Dictionary
Glyph Recipe
Transform

不保存：
Delta Correction
```

匹配成功后，即使原部件和复用部件存在少量差异，也直接使用复用部件。

因此这条路线属于：

```text
有损字体压缩
```

---

## 6.3 算法流程

```text
原始字体
  ↓
解析 glyph outline
  ↓
候选部件分割
  ↓
部件归一化
  ↓
栅格化 bitmap
  ↓
IoU / 相似度匹配
  ↓
建立 Component Dictionary
  ↓
生成 Glyph Recipe
  ↓
输出自定义压缩包
  ↓
解码重建 TTF/OTF
```

---

## 6.4 推荐参数

```text
支持变换：
平移
scaleX
scaleY

暂不支持：
Delta
复杂仿射
大角度旋转
```

匹配阈值建议测试：

```text
0.90
0.92
0.95
0.97
```

---

## 6.5 WebUI 功能

### 上传与配置区

```text
上传 TTF/OTF
选择字符集
选择路线 A
设置匹配阈值
设置是否允许 scaleX / scaleY
设置是否允许小角度旋转
设置栅格化分辨率：64 / 128 / 256
```

### 压缩结果面板

展示：

```text
原始字体大小
压缩包大小
解码后字体大小
压缩率
部件数量
平均每字部件数
raw fallback 比例
平均匹配分数
编码耗时
解码耗时
```

### 单字查看器

输入一个汉字，例如：

```text
湖
谢
想
器
赢
```

显示：

```text
原始字形
重建字形
差异图
部件拆分图
部件编号
每个部件的 transform
每个部件的 matchScore
```

### 部件库查看器

显示：

```text
部件 ID
部件图形
被复用次数
平均匹配分数
最大变形比例
关联汉字列表
```

### 差异热力图

显示：

```text
原图
重建图
不同像素区域
差异百分比
```

---

## 6.6 优点

```text
实现最快
能快速验证压缩潜力
压缩率可能最高
适合网页展示字体、游戏字体、低精度场景
```

---

## 6.7 缺点

```text
会改变原字体细节
不适合要求完全还原的商业字体
误匹配会直接影响字形
```

---

# 路线 B：带 Delta 的近无损部件压缩

## 6.8 目标

在部件复用基础上增加差分修正，使重建字体尽可能接近原字体。

## 6.9 特点

```text
保存：
Component Dictionary
Glyph Recipe
Transform
Delta Correction
```

核心公式：

```text
OriginalComponent ≈ Component × Transform + Delta
```

这条路线可以支持：

```text
近无损压缩
无损压缩，理论上可行
可调节有损程度
```

---

## 6.10 算法流程

```text
原始部件 A
  ↓
匹配到字典部件 B
  ↓
对 B 应用 transform
  ↓
得到近似部件 B'
  ↓
比较 A 与 B'
  ↓
生成 Delta
  ↓
保存 componentId + transform + deltaId
```

---

## 6.11 Delta 类型

### 类型一：点偏移 Delta

适合两个轮廓拓扑结构相似的情况。

```text
pointId → dx/dy
```

优点：

```text
压缩率较好
容易编码
适合同一个部件的轻微变形
```

缺点：

```text
要求轮廓点数量和顺序接近
```

---

### 类型二：额外轮廓 Delta

适合补充小点、小钩、小装饰。

```text
extra contour data
```

优点：

```text
实现简单
适合补局部缺失
```

缺点：

```text
对整体变形帮助有限
```

---

### 类型三：局部替换 Delta

适合部件大部分相同，但某个局部不同。

```text
replace contour segment
```

优点：

```text
还原质量高
```

缺点：

```text
实现复杂
编码格式复杂
```

---

## 6.12 成本判断

每次匹配后必须判断是否值得复用。

```text
reuseCost = componentIdCost + transformCost + deltaCost
rawCost = originalContourCost

if reuseCost < rawCost:
    使用部件复用
else:
    保存 raw contour
```

否则 Delta 可能吃掉全部压缩收益。

---

## 6.13 WebUI 功能

### 上传与配置区

```text
上传 TTF/OTF
选择字符集
选择路线 B
选择 Delta 模式：
  - point-offset
  - extra-contour
  - replace-contour
设置目标还原质量：
  - lossy
  - near-lossless
  - lossless
设置最大允许误差
设置匹配阈值
```

### Delta 查看器

输入一个汉字后显示：

```text
原始部件
复用部件
应用 transform 后的部件
Delta 修正区域
最终重建部件
```

### 成本分析面板

显示：

```text
componentId 成本
transform 成本
delta 成本
raw contour 成本
最终是否采用复用
节省字节数
```

### 质量对比面板

展示：

```text
无 Delta 重建效果
带 Delta 重建效果
原始字形
差异热力图
```

### 批量错误排序

按差异从大到小列出：

```text
差异最大的 100 个字
Delta 最大的 100 个字
复用收益最低的 100 个字
误匹配风险最高的 100 个字
```

---

## 6.14 优点

```text
视觉质量明显更好
可以支持严肃字体场景
可以在压缩率和还原质量之间调节
```

---

## 6.15 缺点

```text
实现复杂
Delta 编码设计难度高
如果 Delta 太大，压缩率可能不如路线 A
```

---

# 路线 C：基于 TrueType Composite Glyph 的标准字体内复用

## 6.16 目标

尽量利用 TTF 标准本身已有的 composite glyph 机制，让复用关系直接存在于字体内部。

这条路线不一定需要自定义浏览器端解码器。

---

## 6.17 核心思路

TrueType 支持一个 glyph 引用其他 glyph，并应用偏移、缩放和变换。

因此可以尝试：

```text
把公共汉字部件提取为内部 glyph
然后让汉字 glyph 通过 composite glyph 引用这些部件
```

流程：

```text
原始字体
  ↓
提取公共部件
  ↓
将部件写入内部 glyph
  ↓
汉字 glyph 改写为 composite glyph
  ↓
生成新的 TTF
  ↓
转 WOFF2
  ↓
浏览器直接加载
```

---

## 6.18 特点

这条路线的最终文件仍然是标准字体：

```text
TTF
WOFF2
```

浏览器可直接通过 FontFace 加载，不需要自定义 WASM 解码。

---

## 6.19 限制

TTF Composite Glyph 适合表达：

```text
引用其他 glyph
x/y 偏移
缩放
简单二维变换
```

但不适合表达：

```text
复杂 Delta
非标准残差编码
有损相似匹配
局部替换
复杂部件压缩索引
```

所以这条路线更适合：

```text
高兼容性
低解码成本
标准字体内部复用
```

不适合极限压缩。

---

## 6.20 WebUI 功能

### 上传与配置区

```text
上传 TTF
选择字符集
选择路线 C
设置部件复用阈值
设置是否允许 scaleX / scaleY
设置是否生成内部 component glyph
设置是否输出 WOFF2
```

### Composite 结构查看器

输入一个汉字后显示：

```text
该汉字是否被 composite 化
引用了哪些 component glyph
每个 component 的偏移
每个 component 的缩放
每个 component 的变换矩阵
```

### 标准字体对比面板

显示：

```text
原始 TTF 大小
Composite 化 TTF 大小
Composite 化 WOFF2 大小
原始 WOFF2 大小
最终节省比例
```

### 浏览器实时预览

使用 FontFace 加载：

```text
原始字体
Composite 化字体
```

并显示：

```text
同一段文本的两种渲染效果
单字放大对比
差异热力图
```

---

## 6.21 优点

```text
最接近标准字体机制
浏览器兼容性最好
前端加载最简单
不需要 WASM 解码
适合工程落地
```

---

## 6.22 缺点

```text
压缩能力受 TrueType composite glyph 限制
不适合复杂 Delta
不适合极限有损压缩
对 OTF/CFF 路线不如 TTF/glyf 自然
```

---

# 7. WebUI 总体设计

## 7.1 页面结构

建议 WebUI 分为以下页面：

```text
1. 字体上传页
2. 压缩配置页
3. 压缩任务页
4. 单字分析页
5. 部件库查看页
6. 差异热力图页
7. 批量报告页
8. 导出下载页
```

---

## 7.2 字体上传页

功能：

```text
上传 TTF/OTF
显示字体基本信息
显示 glyph 数量
显示 cmap 覆盖范围
显示 unitsPerEm
显示字体名称
```

---

## 7.3 压缩配置页

配置项：

```text
选择路线：
  - A：无 Delta 有损压缩
  - B：带 Delta 近无损压缩
  - C：Composite Glyph 标准字体内复用

选择字符集：
  - 自定义输入
  - 常用 500 字
  - GB2312 一级字
  - GB2312 全量
  - U+4E00~U+9FFF

匹配参数：
  - bitmap 分辨率
  - IoU 阈值
  - bbox 权重
  - area 权重
  - 是否允许 scaleX
  - 是否允许 scaleY
  - 是否允许 rotation
  - 最大 rotation 角度
  - 最大 scaleX 范围
  - 最大 scaleY 范围

输出参数：
  - 输出自定义压缩包
  - 输出重建 TTF
  - 输出 WOFF2
  - 输出 JSON 报告
```

---

## 7.4 压缩任务页

显示：

```text
当前阶段
处理进度
已处理 glyph 数
候选部件数
已去重部件数
当前压缩包大小估算
编码耗时
内存占用
```

处理阶段：

```text
解析字体
提取轮廓
生成候选部件
部件匹配
构建字典
生成 recipe
生成 delta
重建字体
生成报告
```

---

## 7.5 单字分析页

输入一个汉字后显示：

```text
原始 glyph
重建 glyph
差异热力图
候选部件拆分
最终采用部件
部件编号
transform 参数
matchScore
delta 信息
raw fallback 状态
```

建议布局：

```text
左：原始字形
中：重建字形
右：差异图
下：部件列表和参数表
```

---

## 7.6 部件库查看页

展示所有部件：

```text
部件 ID
部件预览
复用次数
平均 matchScore
最大 scaleX
最大 scaleY
是否参与 Delta
关联汉字数量
```

支持排序：

```text
按复用次数排序
按节省字节数排序
按平均匹配分排序
按误差最大排序
```

点击某个部件后显示：

```text
该部件被哪些字使用
在每个字中的位置
在每个字中的缩放比例
在每个字中的差异程度
```

---

## 7.7 差异热力图页

支持批量查看：

```text
原始字体渲染
重建字体渲染
差异区域
差异百分比
```

排序维度：

```text
差异最大
差异最小
部件最多
Delta 最大
压缩收益最高
压缩收益最低
```

---

## 7.8 批量报告页

显示全局统计：

```text
原始字体大小
压缩包大小
重建 TTF 大小
重建 WOFF2 大小
压缩率
glyph 数量
component 数量
平均每字 component 数
raw fallback 比例
平均 IoU
最低 IoU
P95 IoU
平均差异率
最大差异率
编码耗时
解码耗时
```

---

## 7.9 导出下载页

支持导出：

```text
自定义压缩包
重建 TTF
重建 WOFF2
压缩报告 JSON
压缩报告 CSV
单字差异截图
部件字典 JSON
Glyph Recipe JSON
```

---

# 8. WebUI 技术栈建议

## 8.1 前端

推荐：

```text
Vite
Vue 3 / React
TypeScript
Canvas / SVG
Web Worker
WASM
IndexedDB
```

其中：

```text
Canvas：用于字体渲染、位图匹配、差异热力图
SVG：用于矢量轮廓预览
Web Worker：避免压缩过程阻塞 UI
WASM：用于高性能字体解析、匹配、重建
IndexedDB：缓存字体、压缩包、测试报告
```

---

## 8.2 后端

第一版可以不要后端，直接浏览器本地处理。

如果字体较大、计算较重，可以增加本地后端：

```text
Python FastAPI
Node.js
Rust Axum
Go
```

推荐实验阶段：

```text
前端 WebUI + Python/Rust 本地服务
```

原因：

```text
字体解析和重建用 Python fontTools 更方便
算法性能部分后续再迁移到 Rust/WASM
```

---

## 8.3 推荐阶段划分

### 阶段一：本地工具 + WebUI 查看

```text
Python 负责字体解析、压缩、重建
WebUI 负责查看结果
```

适合快速验证算法。

### 阶段二：核心算法迁移到 Rust

```text
Rust 实现部件匹配、编码、解码
Python 只保留辅助工具
```

适合提升性能。

### 阶段三：Rust 编译 WASM

```text
浏览器端直接压缩、解码、预览
```

适合最终产品化。

---

# 9. 实验指标

## 9.1 体积指标

```text
原始 TTF 大小
原始 WOFF2 大小
自定义压缩包大小
重建 TTF 大小
重建 WOFF2 大小
Component Dictionary 大小
Glyph Recipe 大小
Transform 数据大小
Delta 数据大小
Raw Fallback 数据大小
```

---

## 9.2 结构指标

```text
汉字数量
原始 glyph 总轮廓数
原始 glyph 总点数
候选部件数量
去重后部件数量
平均每字部件数
复用部件占比
raw fallback 占比
delta 占比
```

---

## 9.3 匹配指标

```text
平均 IoU
最低 IoU
P95 IoU
误匹配数量
人工抽样错误率
平均 matchScore
低分匹配数量
```

---

## 9.4 视觉指标

```text
平均像素差异率
最大像素差异率
小字号差异率
大字号差异率
笔画断裂数量
部件错位数量
字重变化程度
```

---

## 9.5 性能指标

```text
字体解析耗时
部件分割耗时
部件匹配耗时
压缩包生成耗时
解码耗时
字体重建耗时
WebUI 渲染耗时
内存峰值
```

---

# 10. 推荐 MVP 顺序

## MVP 1：字体解析与单字预览

实现：

```text
上传字体
解析 cmap
提取 glyph outline
WebUI 显示单字轮廓
WebUI 显示单字渲染图
```

目标：

```text
确认字体读取和字形显示链路跑通
```

---

## MVP 2：候选部件分割

实现：

```text
按 contour 和 bbox 关系分割候选部件
WebUI 显示部件拆分效果
```

目标：

```text
能看到一个汉字被拆成哪些候选部件
```

---

## MVP 3：位图匹配与部件库

实现：

```text
候选部件归一化
生成 bitmap
计算 IoU
建立部件库
WebUI 显示部件复用情况
```

目标：

```text
验证部件复用是否成立
```

---

## MVP 4：路线 A 压缩包

实现：

```text
Component Dictionary
Glyph Recipe
Transform
Raw Fallback
```

输出：

```text
自定义压缩包
重建 TTF
WebUI 差异图
```

目标：

```text
验证无 Delta 版本压缩率和视觉质量
```

---

## MVP 5：路线 C Composite Glyph

实现：

```text
公共部件写入内部 glyph
原 glyph 改写为 composite glyph
输出标准 TTF / WOFF2
WebUI 查看 composite 结构
```

目标：

```text
验证标准字体内复用是否有实际收益
```

---

## MVP 6：路线 B Delta

实现：

```text
point-offset delta
extra-contour delta
成本判断
WebUI Delta 查看器
```

目标：

```text
验证近无损压缩是否值得继续
```

---

# 11. 三条路线优先级

## 第一优先级：路线 A

```text
无 Delta 的部件复用有损压缩
```

原因：

```text
实现最快
最容易验证压缩潜力
最适合先跑实验
```

---

## 第二优先级：路线 C

```text
TrueType Composite Glyph 标准字体内复用
```

原因：

```text
如果有效，工程落地价值最高
浏览器兼容性最好
不需要自定义前端解码器
```

---

## 第三优先级：路线 B

```text
带 Delta 的近无损压缩
```

原因：

```text
技术潜力最大
但复杂度最高
适合在路线 A 确认可行后继续做
```

---

# 12. 第一版推荐参数

```text
字体：Noto Sans CJK / 思源黑体
字符集：500 常用汉字
路线：A
匹配方式：bitmap IoU + bbox similarity
变换：平移 + scaleX + scaleY
阈值：0.92 / 0.95 两档
输出：自定义 JSON 包 + 重建 TTF
WebUI：单字对比 + 部件库 + 差异热力图
Delta：暂不实现
```

第一版成功标准：

```text
1. 自定义压缩包体积小于原始 WOFF2
2. 重建 TTF 可以正常渲染
3. 大部分高频字视觉差异可接受
4. WebUI 能定位误匹配和高差异字符
```

---

# 13. 最终判断标准

该技术是否值得继续，主要看：

```text
1. 压缩包是否显著小于 WOFF2
2. 重建字形视觉损失是否可控
3. 解码和重建耗时是否可接受
4. WebUI 是否能高效定位错误
5. 是否能在浏览器里稳定加载和缓存
```

如果路线 A 能明显小于 WOFF2，说明部件复用方向成立。

如果路线 C 能明显小于普通 WOFF2，优先考虑标准字体内复用。

如果路线 B 在接近无损的情况下仍能小于 WOFF2，则该方案具备较高商业化价值。
