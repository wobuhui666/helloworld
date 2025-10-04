# AstrBot Markdown 转图片插件

一个用于 AstrBot 的插件，允许 LLM 将复杂的 Markdown 格式内容转换为图片发送，提升消息的可读性。

## 功能特性

- ✅ **Markdown 转图片**：将包含代码块、表格、数学公式等复杂格式的 Markdown 内容转换为清晰图片
- ✅ **LaTeX 数学公式支持**：通过 MathJax 完美渲染数学公式
- ✅ **自动浏览器安装**：插件自动安装和配置 Playwright Chromium 浏览器
- ✅ **智能识别**：LLM 自动判断何时使用图片渲染功能
- ✅ **高清渲染**：支持 2 倍缩放，生成高质量图片
- ✅ **自适应宽度**：支持固定宽度或自适应宽度渲染

## 安装方法

### 前置要求

- Python 3.8+
- AstrBot 框架

### 安装步骤

1. 将插件文件夹放置到 AstrBot 的 `plugins` 目录下
2. 重启 AstrBot 服务
3. 插件会自动安装所需依赖（`mistune` 和 `playwright`）

## 使用方法

### 基本用法

LLM 会自动判断何时使用图片渲染功能。当需要发送包含复杂格式的内容时，LLM 会将 Markdown 内容包裹在 `<md>` 标签中：

```
<md>
# 这是一个标题

这是一个列表:
- 列表项 1
- 列表项 2

这是一个代码块:
```python
def hello_world():
    print("Hello, World!")
```
</md>
```

插件会自动识别并渲染成图片发送。

### 支持的 Markdown 语法

- ✅ 标题（#、##、###）
- ✅ 列表（有序、无序）
- ✅ 代码块（语法高亮）
- ✅ 表格
- ✅ 数学公式（LaTeX）
- ✅ 粗体、斜体、删除线
- ✅ 引用块
- ✅ 水平分割线

### 数学公式示例

```
<md>
行内公式：$a^2 + b^2 = c^2$

独立公式：
$$
\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}
$$
</md>
```

## 配置说明

### 插件配置

插件的配置文件位于 `metadata.yaml`：

```yaml
name: astrbot_plugin_markdown2img
desc: 允许LLM将Markdown文本转换为图片发送。
version: v1.0
author: tosaki
repo: https://github.com/t0saki/astrbot_plugin_markdown2img
```

### 依赖包

- `mistune`: Markdown 解析器
- `playwright`: 浏览器自动化工具（用于渲染）

## 技术实现

### 渲染流程

1. **解析 Markdown**：使用 `mistune` 将 Markdown 转换为 HTML
2. **数学公式处理**：通过 MathJax 渲染 LaTeX 公式
3. **浏览器渲染**：使用 Playwright Chromium 浏览器截图
4. **图片生成**：生成 PNG 格式的高清图片

### 图片缓存

生成的图片存储在 `data/md2img_cache/` 目录下，使用 UUID 作为文件名确保唯一性。
