import os
import re
import uuid
from typing import List

# 确保你已经安装了 mistune 和 playwright
from md2img import sync_markdown_to_image_playwright

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_tools import StarTools


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
        """初始化插件，确保图片缓存目录存在"""
        try:
            os.makedirs(self.IMAGE_CACHE_DIR, exist_ok=True)
            logger.info("Markdown 转图片插件已初始化")
        except Exception as e:
            logger.error(f"创建 md2img 缓存目录失败: {e}")

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
                components = self._process_text_with_markdown(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
        result.chain = new_chain

    def _process_text_with_markdown(self, text: str) -> List:
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
                    sync_markdown_to_image_playwright(
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