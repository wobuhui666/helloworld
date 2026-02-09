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
    "Markdown转图片渲染器 (Playwright 高性能版)",
    "1.3.0",
)
class MarkdownConverterPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.IMAGE_CACHE_DIR = os.path.join(self.DATA_DIR, "md2img_cache")
        
        # Playwright 实例持久化，避免重复启动
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
            
            # 1. 检查并自动安装 Playwright 浏览器依赖 (恢复了这部分详细逻辑)
            await self._ensure_playwright_installed()

            # 2. 预启动浏览器 (关键优化：启动一次，多次使用)
            logger.info("Markdown插件: 正在启动 Playwright Browser...")
            self.playwright = await async_playwright().start()
            
            # 启动配置：无头模式，禁用沙箱以适应 Docker/Linux 环境
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            logger.info("Markdown插件: 初始化完成，浏览器已就绪。")

        except Exception as e:
            logger.error(f"Markdown插件初始化失败: {e}")
            logger.error("如果是因为缺少浏览器，请尝试手动运行: playwright install chromium")

    async def terminate(self):
        """插件卸载或重载时清理资源"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Markdown插件: 已停止")

    async def _ensure_playwright_installed(self):
        """
        自动检测并安装 Playwright 的 Chromium 浏览器和系统依赖。
        (这是之前缺少的详细安装逻辑)
        """
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
                err_msg = stderr.decode('utf-8', errors='ignore')
                logger.error(f"{desc} 安装失败: {err_msg}")
                return False
            
            if "up to date" in output:
                logger.info(f"{desc} 已是最新。")
            else:
                logger.info(f"{desc} 安装/更新成功。")
            return True

        try:
            # 1. 安装 Chromium 浏览器
            # 对应命令: python -m playwright install chromium
            await run_cmd(
                [sys.executable, "-m", "playwright", "install", "chromium"], 
                "Playwright Chromium Browser"
            )
            
            # 2. (可选) 安装系统依赖，通常在 Linux 上需要
            # 对应命令: python -m playwright install-deps
            # 注意：这通常需要 sudo 权限，如果在 Docker 中可能需要手动执行，这里尝试执行一下
            if sys.platform.startswith("linux"):
                logger.info("检测到 Linux 环境，尝试安装系统依赖...")
                # 这里不强制阻塞，失败了只记录日志，防止因无 sudo 权限卡死
                await run_cmd(
                    [sys.executable, "-m", "playwright", "install-deps"], 
                    "System Dependencies"
                )

        except Exception as e:
            logger.warning(f"自动安装 Playwright 依赖时发生异常 (可忽略): {e}")

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入 System Prompt，强制 LLM 分离纯文本和渲染内容"""
        instruction_prompt = """
[排版强制指令]
当你的回答包含**数学公式 (LaTeX)**、**复杂代码**、**表格**或**长推导过程**时，必须严格遵守以下规则：

1. **封装渲染内容**：将所有公式、代码块、表格、长文本推导，全部包裹在 `<md>` 和 `</md>` 标签之间。
2. **标签内部 (渲染区)**：在 `<md>` 内部，请尽情使用 Markdown 和 LaTeX 语法，确保排版美观。
3. **标签外部 (纯文本区)**：在 `<md>` 标签**外部**的文字（例如简短的介绍语、结论或过渡句），必须是**纯文本 (Plain Text)**。
   - 🚫 **严禁**在标签外部使用任何 Markdown 标记（不要使用 **加粗**、# 标题、> 引用、列表符等）。
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
        # 将指令追加到 system prompt 的末尾
        req.system_prompt += f"\n\n{instruction_prompt}"

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """结果处理：解析 <md> 标签并替换为图片"""
        result = event.get_result()
        new_chain = []
        
        for item in result.chain:
            if isinstance(item, Plain):
                # 核心逻辑：分割文本并渲染
                components = await self._process_text_with_markdown(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
                
        result.chain = new_chain

    async def _process_text_with_markdown(self, text: str) -> List:
        """解析文本中的 <md> 标签"""
        components = []
        # 正则：非贪婪匹配 <md>...</md>，re.S 让 . 匹配换行符
        pattern = r"(<md>.*?</md>)"
        parts = re.split(pattern, text, flags=re.S)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith("<md>") and part.endswith("</md>"):
                md_content = part[4:-5].strip() # 去除 <md> 和 </md>
                if not md_content:
                    continue

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
                # 标签外部的内容，保持为纯文本
                components.append(Plain(part))

        return components

    async def _render_image(self, md_text: str, output_path: str, min_width: int = 600):
        """核心渲染逻辑"""
        # 1. 确保浏览器存活 (断线重连机制)
        if not self.browser or not self.browser.is_connected():
            logger.warning("Browser 断开或未初始化，正在重连...")
            await self.initialize()
            if not self.browser:
                raise Exception("Browser 初始化失败，无法渲染图片。")

        # 2. Markdown -> HTML 片段
        html_body = self.markdown_parser(md_text)

        # 3. HTML 片段 -> 完整 HTML (含 CSS 和 JS)
        full_html = self._get_html_template(html_body, min_width)

        # 4. 创建 Page
        # 优化点：context 可以在 browser 生命周期内复用，这里简单起见每次新建 context 也没问题，比新建 browser 快得多
        # Viewport 设置较大，防止宽公式被强制换行
        context = await self.browser.new_context(
            device_scale_factor=2, # 2倍缩放，高清
            viewport={'width': 1600, 'height': 1200} 
        )
        page = await context.new_page()

        try:
            await page.set_content(full_html, wait_until="networkidle")

            # 5. 等待 MathJax 渲染完成 (解决 Loading 条和公式未渲染问题)
            await page.evaluate("""
                () => {
                    if (window.MathJax) {
                        return MathJax.typesetPromise();
                    }
                }
            """)
            
            # 6. 短暂等待布局稳定 (防止 fontdata.js 加载造成的微小回流)
            await asyncio.sleep(0.3)

            # 7. 截图 (只截 body 部分，fit-content 会保证尺寸正确)
            body = await page.query_selector("body")
            if body:
                await body.screenshot(path=output_path)
            else:
                raise Exception("渲染错误：页面为空")

        finally:
            await page.close()
            await context.close()

    def _get_html_template(self, content: str, min_width: int) -> str:
        """
        生成 HTML 模板
        包含：MathJax 3 配置、GitHub 风格 CSS、自适应宽度修正、隐藏 Loading 条
        """
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
                startup: {{ typeset: false }} // 手动触发渲染
            }};
            </script>
            <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <style>
                /* 修复 1: 彻底隐藏 MathJax Loading 提示条 */
                #MathJax_Message {{
                    display: none !important;
                    visibility: hidden !important;
                    opacity: 0 !important;
                }}

                body {{
                    /* 修复 2: 自适应宽度布局 */
                    width: fit-content;
                    min-width: {min_width}px;
                    max-width: 1500px; /* 防止过宽 */
                    
                    padding: 20px;
                    margin: 0;
                    background-color: white;
                    
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                    font-size: 16px;
                    line-height: 1.6;
                    color: #24292e;
                    
                    /* 让截图紧贴内容边缘 */
                    display: inline-block;
                }}

                /* 图片自适应 */
                img {{ max-width: 100%; height: auto; }}
                
                /* 代码块样式 */
                pre {{
                    background-color: #f6f8fa;
                    border-radius: 6px;
                    padding: 16px;
                    overflow: auto;
                    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
                    font-size: 85%;
                    line-height: 1.45;
                }}
                
                /* 表格样式 */
                table {{ border-collapse: collapse; margin-bottom: 16px; min-width: 50%; }}
                th, td {{ border: 1px solid #dfe2e5; padding: 6px 13px; }}
                tr:nth-child(2n) {{ background-color: #f6f8fa; }}
                th {{ font-weight: 600; background-color: #f6f8fa; }}
                
                /* 引用样式 */
                blockquote {{
                    margin: 0;
                    padding: 0 1em;
                    color: #6a737d;
                    border-left: 0.25em solid #dfe2e5;
                }}
                
                h1, h2, h3 {{ border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
            </style>
        </head>
        <body>
            {content}
        </body>
        </html>
        """
