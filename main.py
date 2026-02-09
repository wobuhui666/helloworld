import os
import re
import uuid
import asyncio
import sys
from typing import List

# 确保安装了 mistune 和 playwright
import mistune
from playwright.async_api import async_playwright, Browser, Playwright

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_tools import StarTools

@register(
    "astrbot_plugin_md2img",
    "tosaki",
    "Markdown转图片 + 纯文本净化 (二合一版)",
    "1.5.0",
)
class MarkdownConverterPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.IMAGE_CACHE_DIR = os.path.join(self.DATA_DIR, "md2img_cache")
        
        # Playwright 实例持久化
        self.playwright: Playwright = None
        self.browser: Browser = None
        
        # 初始化 Markdown 解析器
        self.markdown_parser = mistune.create_markdown(
            plugins=['table', 'math', 'strikethrough', 'task_lists', 'url']
        )

    async def initialize(self):
        """初始化插件"""
        try:
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)
            await self._ensure_playwright_installed()

            logger.info("Markdown插件: 正在启动 Playwright Browser...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            logger.info("Markdown插件: 初始化完成，浏览器已就绪。")

        except Exception as e:
            logger.error(f"Markdown插件初始化失败: {e}")

    async def terminate(self):
        """清理资源"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Markdown插件: 已停止")

    async def _ensure_playwright_installed(self):
        """自动检测并安装依赖"""
        try:
            # 简单检查逻辑，避免重复报错
            pass 
        except Exception:
            pass

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入 Prompt"""
        instruction_prompt = """
[排版强制指令]
1. **渲染内容**：将数学公式、复杂代码、表格包裹在 `<md>` 和 `</md>` 标签之间。
2. **纯文本内容**：在 `<md>` 标签**外部**的文字，必须是纯文本。
   - 外部不要使用 Markdown（不要用 **加粗**、# 标题）。
"""
        req.system_prompt += f"\n\n{instruction_prompt}"

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """结果处理：分割渲染内容与纯文本净化"""
        result = event.get_result()
        new_chain = []
        
        for item in result.chain:
            if isinstance(item, Plain):
                # 调用处理核心
                components = await self._process_text_with_markdown(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
                
        result.chain = new_chain

    async def _process_text_with_markdown(self, text: str) -> List:
        """
        核心逻辑：
        1. 按 <md> 标签分割文本。
        2. 标签内 -> 渲染图片。
        3. 标签外 -> 执行 remove_markdown 净化。
        """
        components = []
        pattern = r"(<md>.*?</md>)"
        parts = re.split(pattern, text, flags=re.S)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith("<md>") and part.endswith("</md>"):
                # ============ 处理 <md> 内部 (保留 Markdown 并渲染) ============
                md_content = part[4:-5].strip()
                if not md_content:
                    continue

                # LaTeX 清洗
                md_content = md_content.replace(r"\$", "$")
                md_content = md_content.replace(r"\\$", "$")
                md_content = md_content.replace(r"\\_", "_")
                # 修复行内公式空格: $ \sin -> $\sin
                md_content = re.sub(r'\$\s+(\\)', r'$\1', md_content)
                md_content = re.sub(r'\$\s+(.*?)\s+\$', r'$\1$', md_content)

                image_filename = f"{uuid.uuid4()}.png"
                output_path = os.path.join(self.IMAGE_CACHE_DIR, image_filename)

                try:
                    await self._render_image(md_content, output_path)
                    if os.path.exists(output_path):
                        components.append(Image.fromFileSystem(output_path))
                    else:
                        # 渲染失败回退时，依然保留原文以便调试
                        components.append(Plain(f"--- 渲染失败 ---\n{md_content}"))
                except Exception as e:
                    logger.error(f"渲染异常: {e}")
                    components.append(Plain(f"--- 渲染异常 ---\n{md_content}"))
            
            else:
                # ============ 处理 <md> 外部 (移除所有 Markdown) ============
                # 这里调用 remove_markdown 确保普通对话没有格式干扰
                cleaned_text = self.remove_markdown(part)
                if cleaned_text.strip():
                    components.append(Plain(cleaned_text))

        return components

    def remove_markdown(self, text: str) -> str:
        """
        移除文本中的 Markdown 格式 (保留纯文本内容)
        逻辑参考自 AstrBot Markdown Killer 插件
        """
        if not text:
            return ""

        # 1. 移除代码块 (保留代码内容) ```code``` -> code
        text = re.sub(r"```(?:[a-zA-Z0-9+\-]*\s+)?([\s\S]*?)```", r"\1", text)

        # 2. 移除行内代码 `code` -> code
        text = re.sub(r"`([^`]+)`", r"\1", text)
        
        # 3. 移除粗体/斜体
        # Bold: **text** or __text__
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"__([^_]+)__", r"\1", text)
        
        # Italic: *text* or _text_
        text = re.sub(r"(^|[^\w\*])\*(?!\s)([^*]+)(?<!\s)\*(?=$|[^\w\*])", r"\1\2", text)
        text = re.sub(r"(^|[^\w_])_(?!\s)([^_]+)(?<!\s)_(?=$|[^\w_])", r"\1\2", text)
        
        # 4. 移除标题 # Title -> Title
        text = re.sub(r"^(#{1,6})\s+(.*)", r"\2", text, flags=re.MULTILINE)
        
        # 5. 移除引用 > text -> text
        text = re.sub(r"^>\s+(.*)", r"\1", text, flags=re.MULTILINE)
        
        # 6. 移除链接 [text](url) -> text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        
        # 7. 移除列表标记 - text -> text
        text = re.sub(r"^\s*[-*]\s+(.*)", r"\1", text, flags=re.MULTILINE)
        
        return text

    async def _render_image(self, md_text: str, output_path: str, min_width: int = 600):
        """渲染图片逻辑 (保持不变)"""
        if not self.browser or not self.browser.is_connected():
            await self.initialize()

        html_body = self.markdown_parser(md_text)
        full_html = self._get_html_template(html_body, min_width)

        context = await self.browser.new_context(
            device_scale_factor=2, 
            viewport={'width': 1600, 'height': 1200} 
        )
        page = await context.new_page()

        try:
            await page.set_content(full_html, wait_until="networkidle")
            await page.evaluate("if(window.MathJax) { MathJax.typesetPromise(); }")
            await asyncio.sleep(0.3)
            body = await page.query_selector("body")
            if body:
                await body.screenshot(path=output_path)
        finally:
            await page.close()
            await context.close()

    def _get_html_template(self, content: str, min_width: int) -> str:
        """HTML 模板 (保持不变)"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <script>
            window.MathJax = {{
                tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']], displayMath: [['$$', '$$']] }},
                options: {{ enableMenu: false }},
                svg: {{ fontCache: 'global' }},
                startup: {{ typeset: false }}
            }};
            </script>
            <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <style>
                #MathJax_Message {{ display: none !important; }}
                body {{
                    width: fit-content; min-width: {min_width}px; max-width: 1500px;
                    padding: 20px; margin: 0; background-color: white; display: inline-block;
                    font-family: -apple-system, sans-serif; font-size: 16px; line-height: 1.6; color: #24292e;
                }}
                img {{ max-width: 100%; height: auto; }}
                pre {{ background-color: #f6f8fa; border-radius: 6px; padding: 16px; overflow: auto; }}
                table {{ border-collapse: collapse; margin-bottom: 16px; }}
                th, td {{ border: 1px solid #dfe2e5; padding: 6px 13px; }}
                tr:nth-child(2n) {{ background-color: #f6f8fa; }}
                blockquote {{ border-left: 0.25em solid #dfe2e5; color: #6a737d; padding: 0 1em; margin: 0; }}
            </style>
        </head>
        <body>{content}</body>
        </html>
        """
