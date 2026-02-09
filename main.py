import os
import re
import uuid
import asyncio
import sys
import subprocess
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
    "Markdown转图片渲染器 (终极修复版)",
    "1.4.0",
)
class MarkdownConverterPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.IMAGE_CACHE_DIR = os.path.join(self.DATA_DIR, "md2img_cache")
        
        # Playwright 实例持久化
        self.playwright: Playwright = None
        self.browser: Browser = None
        
        # 初始化 Markdown 解析器 (启用数学公式、表格等插件)
        self.markdown_parser = mistune.create_markdown(
            plugins=['table', 'math', 'strikethrough', 'task_lists', 'url']
        )

    async def initialize(self):
        """初始化插件：检查依赖、创建目录并启动浏览器"""
        try:
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)
            
            # 1. 检查并自动安装 Playwright 浏览器依赖
            await self._ensure_playwright_installed()

            # 2. 预启动浏览器 (关键优化)
            logger.info("Markdown插件: 正在启动 Playwright Browser...")
            self.playwright = await async_playwright().start()
            
            # 启动配置：无头模式，禁用沙箱
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            logger.info("Markdown插件: 初始化完成，浏览器已就绪。")

        except Exception as e:
            logger.error(f"Markdown插件初始化失败: {e}")
            logger.error("请尝试手动运行: playwright install chromium")

    async def terminate(self):
        """插件卸载时清理资源"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Markdown插件: 已停止")

    async def _ensure_playwright_installed(self):
        """自动检测并安装 Playwright 依赖"""
        async def run_cmd(cmd: list, desc: str):
            logger.info(f"正在检查/安装 {desc}...")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            output = stdout.decode('utf-8', errors='ignore')
            
            if process.returncode != 0:
                logger.error(f"{desc} 安装失败: {stderr.decode('utf-8', errors='ignore')}")
                return False
            
            if "up to date" in output:
                logger.info(f"{desc} 已是最新。")
            else:
                logger.info(f"{desc} 安装/更新成功。")
            return True

        try:
            await run_cmd(
                [sys.executable, "-m", "playwright", "install", "chromium"], 
                "Playwright Chromium Browser"
            )
            if sys.platform.startswith("linux"):
                # Linux 环境下尝试安装系统依赖
                await run_cmd(
                    [sys.executable, "-m", "playwright", "install-deps"], 
                    "System Dependencies"
                )
        except Exception as e:
            logger.warning(f"自动安装依赖时发生异常 (可忽略): {e}")

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入 System Prompt"""
        instruction_prompt = """
[排版强制指令]
当你的回答包含**数学公式 (LaTeX)**、**复杂代码**、**表格**或**长推导过程**时，必须严格遵守以下规则：

1. **封装渲染内容**：将所有公式、代码块、表格、长文本推导，全部包裹在 `<md>` 和 `</md>` 标签之间。
2. **标签内部 (渲染区)**：在 `<md>` 内部，请尽情使用 Markdown 和 LaTeX 语法。
3. **标签外部 (纯文本区)**：在 `<md>` 标签**外部**的文字，必须是**纯文本 (Plain Text)**。
   - 🚫 **严禁**在标签外部使用 Markdown 标记（不要用 **加粗**、# 标题等）。
   - ✅ 标签外部只能包含普通文字、标点和换行。

[标准范例]
用户：计算圆的面积。
助手回复：
圆的面积计算公式如下：
<md>
### 推导过程
若圆的半径为 $r$，则面积 $S$ 为：
$$ S = \pi r^2 $$
</md>
希望这个公式对你有用。
"""
        req.system_prompt += f"\n\n{instruction_prompt}"

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """结果处理：解析标签并渲染"""
        result = event.get_result()
        new_chain = []
        
        for item in result.chain:
            if isinstance(item, Plain):
                components = await self._process_text_with_markdown(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
                
        result.chain = new_chain

    async def _process_text_with_markdown(self, text: str) -> List:
        """解析文本，清洗 LaTeX 语法，并调用渲染"""
        components = []
        pattern = r"(<md>.*?</md>)"
        parts = re.split(pattern, text, flags=re.S)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith("<md>") and part.endswith("</md>"):
                md_content = part[4:-5].strip()
                if not md_content:
                    continue

                # ================= 核心修复：清洗 LaTeX =================
                # 1. 还原被转义的美元符号: \$ -> $ (解决公式不显示问题)
                md_content = md_content.replace(r"\$", "$")
                # 2. 还原双重转义
                md_content = md_content.replace(r"\\$", "$")
                md_content = md_content.replace(r"\\_", "_")
                # ======================================================

                image_filename = f"{uuid.uuid4()}.png"
                output_path = os.path.join(self.IMAGE_CACHE_DIR, image_filename)

                try:
                    await self._render_image(md_content, output_path)
                    if os.path.exists(output_path):
                        components.append(Image.fromFileSystem(output_path))
                    else:
                        components.append(Plain(f"--- 渲染失败 (文件未生成) ---\n{md_content}"))
                except Exception as e:
                    logger.error(f"Markdown 渲染异常: {e}")
                    components.append(Plain(f"--- 渲染异常 ---\n{md_content}"))
            else:
                components.append(Plain(part))

        return components

    async def _render_image(self, md_text: str, output_path: str, min_width: int = 600):
        """Playwright 渲染核心逻辑"""
        # 1. 确保浏览器存活
        if not self.browser or not self.browser.is_connected():
            logger.warning("Browser 断开，正在重连...")
            await self.initialize()
            if not self.browser:
                raise Exception("Browser 初始化失败")

        # 2. Markdown -> HTML
        html_body = self.markdown_parser(md_text)
        full_html = self._get_html_template(html_body, min_width)

        # 3. 创建 Page (使用大 Viewport 防止宽公式强制换行)
        context = await self.browser.new_context(
            device_scale_factor=2, 
            viewport={'width': 1600, 'height': 1200} 
        )
        page = await context.new_page()

        try:
            await page.set_content(full_html, wait_until="networkidle")

            # 4. 显式触发 MathJax 渲染
            await page.evaluate("""
                () => {
                    if (window.MathJax) {
                        return MathJax.typesetPromise();
                    }
                }
            """)
            
            # 5. 等待布局稳定
            await asyncio.sleep(0.3)

            # 6. 截图 body
            body = await page.query_selector("body")
            if body:
                await body.screenshot(path=output_path)
            else:
                raise Exception("页面渲染为空")

        finally:
            await page.close()
            await context.close()

    def _get_html_template(self, content: str, min_width: int) -> str:
        """生成 HTML 模板：含 MathJax 配置和 CSS 修复"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <script>
            window.MathJax = {{
                tex: {{
                    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
                }},
                options: {{ enableMenu: false }},
                svg: {{ fontCache: 'global' }},
                startup: {{ typeset: false }} 
            }};
            </script>
            <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <style>
                /* 修复: 隐藏 MathJax Loading 条 */
                #MathJax_Message {{
                    display: none !important;
                    visibility: hidden !important;
                }}

                body {{
                    /* 修复: 自适应宽度，防止截断 */
                    width: fit-content;
                    min-width: {min_width}px;
                    max-width: 1500px;
                    
                    padding: 20px;
                    margin: 0;
                    background-color: white;
                    display: inline-block; /* 配合 fit-content */
                    
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                    font-size: 16px;
                    line-height: 1.6;
                    color: #24292e;
                }}

                img {{ max-width: 100%; height: auto; }}
                
                pre {{
                    background-color: #f6f8fa;
                    border-radius: 6px;
                    padding: 16px;
                    overflow: auto;
                    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
                    font-size: 85%;
                }}
                
                table {{ border-collapse: collapse; margin-bottom: 16px; }}
                th, td {{ border: 1px solid #dfe2e5; padding: 6px 13px; }}
                tr:nth-child(2n) {{ background-color: #f6f8fa; }}
                
                blockquote {{
                    margin: 0;
                    padding: 0 1em;
                    color: #6a737d;
                    border-left: 0.25em solid #dfe2e5;
                }}
            </style>
        </head>
        <body>
            {content}
        </body>
        </html>
        """
