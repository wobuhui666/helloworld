import os
import re
import uuid
from typing import List


from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_tools import StarTools

# 确保你已经安装了 mistune 和 playwright
import mistune
import asyncio
from playwright.async_api import async_playwright
import uuid

import subprocess
import sys


async def markdown_to_image_playwright(
    md_text: str,
    output_image_path: str,
    scale: int = 2,
    width: int = None
):
    """
    使用 Playwright 将包含 LaTeX 的 Markdown 转换为图片。

    :param md_text: Markdown 格式的字符串。
    :param output_image_path: 图片输出路径。
    :param scale: 渲染的缩放因子。大于 1 的值可以有效提升清晰度和抗锯齿效果。
    :param width: 图片内容的固定宽度（单位：像素）。如果为 None，则宽度自适应内容。
    """
    # 1. 根据是否提供了 width 参数，动态生成 body 的宽度样式
    width_style = ""
    if width:
        # box-sizing: border-box 可确保 padding 包含在设定的 width 内
        width_style = f"width: {width}px; box-sizing: border-box;"

    # 2. 改进的 HTML 模板，为 body 样式增加了一个占位符 {width_style}
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Markdown Render</title>
        <style>
            body {{
                {width_style} /* 宽度样式将在这里注入 */
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
                padding: 25px;
                display: inline-block; /* 让截图尺寸自适应内容 */
                font-size: 16px;

                /* 开启更平滑的字体渲染 */
                -webkit-font-smoothing: antialiased;
                -moz-osx-font-smoothing: grayscale;
                text-rendering: optimizeLegibility;
            }}
            /* 为代码块添加一些样式 */
            pre {{
                background-color: #f6f8fa;
                border-radius: 6px;
                padding: 16px;
                overflow: auto;
                font-size: 85%;
                line-height: 1.45;
            }}
            code {{
                font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            }}
        </style>
        <script type="text/javascript" async
            src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js?config=TeX-MML-AM_CHTML">
        </script>
        <script type="text/x-mathjax-config">
            MathJax.Hub.Config({{
                tex2jax: {{
                    inlineMath: [['$','$']],
                    displayMath: [['$$','$$']],
                }},
                "HTML-CSS": {{
                    scale: 100,
                    linebreaks: {{ automatic: true }}
                }},
                SVG: {{ linebreaks: {{ automatic: true }} }}
            }});
        </script>
    </head>
    <body>
        {content}
    </body>
    </html>
    """

    # 3. 将 Markdown 转换为 HTML
    html_content = mistune.html(md_text)

    # 4. 填充 HTML 模板，同时传入内容和宽度样式
    full_html = html_template.format(
        content=html_content, width_style=width_style)

    # 5. 使用 Playwright 进行截图
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            device_scale_factor=scale
        )
        page = await context.new_page()

        await page.set_content(full_html, wait_until="networkidle")

        # 更稳健地等待 MathJax 渲染完成
        try:
            await page.evaluate("MathJax.Hub.Queue(['Typeset', MathJax.Hub])")
            await page.wait_for_function("typeof MathJax.Hub.Queue.running === 'undefined' || MathJax.Hub.Queue.running === 0")
        except Exception as e:
            print(f"等待 MathJax 时出错 (可能是页面加载太快): {e}")

        element_handle = await page.query_selector('body')
        if not element_handle:
            raise Exception("无法找到 <body> 元素进行截图。")

        await element_handle.screenshot(path=output_image_path)
        await browser.close()
        print(f"图片已保存到: {output_image_path}")


# --- 示例 ---
markdown_string = """
# Playwright 渲染测试

这是一个宽度被设置为 600px 的示例。当文本内容足够长时，它会自动换行以适应设定的宽度。

行内公式 $a^2 + b^2 = c^2$。

独立公式：
$$
\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\pi}}{2}
$$

以及一段 C++ 代码:
```cpp
#include <iostream>

int main() {
    // 这是一段注释，用来增加代码块的宽度，以测试在固定宽度下的显示效果。
    std::cout << "Hello, C++! This is a longer line to demonstrate wrapping or scrolling." << std::endl;
    return 0;
}
"""


def sync_markdown_to_image_playwright(md_text, output_image_path, scale=2, width=None):
    asyncio.run(markdown_to_image_playwright(
        md_text, output_image_path, scale, width))


if __name__ == "__main__":
    # 生成一个固定宽度的图片
    output_file_fixed_width = f"markdown_width_{uuid.uuid4().hex[:6]}.png"
    asyncio.run(markdown_to_image_playwright(
        markdown_string,
        output_file_fixed_width,
        scale=2,
        width=600  # 设置宽度为 600px
    ))

    # 生成一个自适应宽度的图片(不设置 width 参数)
    output_file_auto_width = f"markdown_auto_{uuid.uuid4().hex[:6]}.png"
    asyncio.run(markdown_to_image_playwright(
        markdown_string,
        output_file_auto_width,
        scale=2
    ))


@register(
    "astrbot_plugin_md2img",
    "tosaki",  # Or your name
    "允许LLM将Markdown文本转换为图片发送",
    "1.0.0",
)
class MarkdownConverterPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        # 创建一个专门用于存放生成图片的缓存目录
        self.IMAGE_CACHE_DIR = os.path.join(self.DATA_DIR, "md2img_cache")

    async def initialize(self):
        """初始化插件，确保图片缓存目录和 Playwright 浏览器存在"""
        try:
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)

            # --- [核心修改] Playwright 浏览器自动安装 ---
            logger.info("正在检查 Playwright 浏览器依赖...")
            try:
                # 使用 [sys.executable, "-m", ...] 可以确保我们用的是当前 Python 环境的 pip/playwright
                # 明确指定 'chromium'，因为你的代码只用到了它，这样可以避免下载所有浏览器，速度更快
                # `check=True` 会在命令执行失败时抛出异常
                # `capture_output=True` 和 `text=True` 可以捕获命令的输出，避免在控制台刷屏
                result = subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True,
                    capture_output=True,
                    text=True
                )
                # 如果 stdout 包含 "up to date"，说明是跳过下载的，可以不打印
                if "up to date" not in result.stdout:
                    logger.info(
                        f"Playwright Chromium 安装/更新完成。\n{result.stdout}")
                else:
                    logger.info("Playwright Chromium 浏览器已存在，无需下载。")

            except subprocess.CalledProcessError as e:
                # 如果安装命令本身执行失败了
                logger.error(f"自动安装 Playwright 浏览器失败，返回码: {e.returncode}")
                logger.error(f"错误输出: \n{e.stderr}")
            except FileNotFoundError:
                # 如果 python -m playwright 这个命令都找不到
                logger.error(
                    "无法执行 Playwright 安装命令。请检查 Playwright Python 包是否已正确安装。")
            # --- [核心修改结束] ---

            logger.info("Markdown 转图片插件已初始化")

        except Exception as e:
            logger.error(f"插件初始化过程中发生未知错误: {e}")

    async def terminate(self):
        """插件停用时调用"""
        logger.info("Markdown 转图片插件已停止")

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """向 LLM 注入使用 Markdown 转图片功能的指令"""
        instruction_prompt = """
当你需要发送包含复杂格式（如代码块、表格、嵌套列表等）的内容时，为了获得更好的显示效果，你可以将这部分内容渲染成一张图片。

使用规则：
1. 将你需要转换为图片的 Markdown 全文内容包裹在 `<md>` 和 `</md>` 标签之间。
2. LLM应自行判断何时使用该功能，通常用于格式复杂、纯文本难以阅读的场景。
3. 标签内的内容应为完整的、格式正确的 Markdown 文本。

例如：
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
"""
        # 将指令添加到 system prompt 的末尾
        req.system_prompt += f"\\n\\n{instruction_prompt}"

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """将原始响应暂存，以便后续处理"""
        # 这一步是为了将 LLM 的原始响应（可能包含<md>标签）保存到事件上下文中
        event.set_extra("raw_llm_completion_text", resp.completion_text)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在最终消息链生成阶段，解析并替换 <md> 标签"""
        result = event.get_result()
        chain = result.chain
        new_chain = []
        for item in chain:
            # 我们只处理纯文本部分
            if isinstance(item, Plain):
                # 调用核心处理函数
                components = await self._process_text_with_markdown(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
        result.chain = new_chain

    async def _process_text_with_markdown(self, text: str) -> List:
        """
        处理包含 <md>...</md> 标签的文本。
        将其分割成 Plain 和 Image 组件的列表。
        """
        components = []
        # 使用 re.split 来分割文本，保留分隔符（<md>...</md>）
        # re.DOTALL 使得 '.' 可以匹配包括换行符在内的任意字符
        pattern = r"(<md>.*?</md>)"
        parts = re.split(pattern, text, flags=re.DOTALL)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # 检查当前部分是否是 <md> 标签
            if part.startswith("<md>") and part.endswith("</md>"):
                # 提取标签内的 Markdown 内容
                md_content = part[4:-5].strip()
                if not md_content:
                    continue

                # 生成一个唯一的图片文件名
                image_filename = f"{uuid.uuid4()}.png"
                output_path = os.path.join(self.IMAGE_CACHE_DIR, image_filename)

                try:
                    # 调用库函数生成图片
                    await markdown_to_image_playwright(
                        md_text=md_content,
                        output_image_path=output_path,
                        scale=2  # 2倍缩放以获得更高清的图片
                    )

                    if os.path.exists(output_path):
                        # 如果图片成功生成，则添加到组件列表中
                        components.append(Image.fromFileSystem(output_path))
                    else:
                        # 如果生成失败，则将原始 Markdown 内容作为纯文本发送
                        logger.error(f"Markdown 图片生成失败，但文件未找到: {output_path}")
                        components.append(Plain(f"--- Markdown 渲染失败 ---\n{md_content}"))
                except Exception as e:
                    logger.error(f"调用 sync_markdown_to_image_playwright 异常: {e}")
                    # 如果转换过程中发生异常，也回退到纯文本
                    components.append(Plain(f"--- Markdown 渲染异常 ---\n{md_content}"))
            else:
                # 如果不是标签，就是普通文本
                components.append(Plain(part))

        return components