# -*- coding: utf-8 -*-
# 文件名: firewall_resetter.py (可以这样命名)

# 导入 AstrBot 框架的必要模块
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 导入我们需要的标准库
import telnetlib
import time
import asyncio  # 必须导入 asyncio 来处理阻塞操作

# --- 插件注册信息 ---
@register(
    "firewall_resetter",           # 插件的唯一ID
    "YourName",                    # 你的名字
    "一个通过Telnet重置防火墙的插件", # 插件描述
    "1.0.0"                        # 插件版本
)
class FirewallResetPlugin(Star):
    """
    这个插件监听 /checkwall 命令，并通过 Telnet 连接到指定设备执行一系列命令来重置防火墙。
    """
    def __init__(self, context: Context):
        super().__init__(context)

        # --- 将配置信息放在插件类内部，方便管理 ---
        self.TELNET_CONFIG = {
            "host": "192.168.1.1",
            "port": 23,
            # !!!【请务必最后一次确认】这里的用户名和密码完全正确 !!!
            "username": "admin",  # <-- 修改为您的登录用户名
            "password": "admin",  # <-- 修改为您的登录密码
        }

        self.DELAYS = {
            "afterConnect": 1.0,
            "afterUsername": 0.5,
            "afterPassword": 1.5,
            "betweenCommands": 0.5,
        }

        self.COMMANDS_TO_RUN = [
            "ip6tables -F",
            "ip6tables -P INPUT ACCEPT",
            "ip6tables -P FORWARD ACCEPT",
            "ip6tables -P OUTPUT ACCEPT",
        ]

    # 这是一个同步的、会阻塞的方法。我们将在一个单独的线程中运行它。
    # 它不使用 async def 定义。
    # 它返回一个元组 (bool, str)，分别代表是否成功和操作日志。
    def _run_telnet_task(self) -> tuple[bool, str]:
        """
        核心的 Telnet 操作逻辑。
        这个方法会阻塞，所以不能直接在 async handler 中调用。
        """
        log_lines = []  # 用来收集所有日志信息，最后一次性返回
        host = self.TELNET_CONFIG["host"]
        port = self.TELNET_CONFIG["port"]
        username = self.TELNET_CONFIG["username"]
        password = self.TELNET_CONFIG["password"]

        log_lines.append(f"[*] 准备通过 Telnet 连接到 {host}:{port}...")
        
        tn = None
        try:
            # --- 1. 连接 ---
            tn = telnetlib.Telnet(host, port, timeout=10)
            log_lines.append("[+] 连接成功。")

            time.sleep(self.DELAYS["afterConnect"])

            # --- 2. 登录 ---
            log_lines.append(f"[>] 发送用户名: {username}")
            tn.write(f"{username}\n".encode('ascii'))
            time.sleep(self.DELAYS["afterUsername"])

            log_lines.append("[>] 发送密码...")
            tn.write(f"{password}\n".encode('ascii'))
            time.sleep(self.DELAYS["afterPassword"])
            
            # 读取登录后的输出，帮助判断是否成功
            login_output = tn.read_very_eager().decode('utf-8', errors='ignore')
            if login_output:
                log_lines.append('--- 登录后设备输出 ---')
                log_lines.append(login_output.strip())
                log_lines.append('--- 输出结束 ---\n')

            # --- 3. 执行命令 ---
            log_lines.append('[+] 开始按顺序执行命令...')
            for cmd in self.COMMANDS_TO_RUN:
                log_lines.append(f"    -> 正在执行: {cmd}")
                tn.write(f"{cmd}\n".encode('ascii'))
                time.sleep(self.DELAYS["betweenCommands"])
                # 读取命令执行后的输出
                cmd_output = tn.read_very_eager().decode('utf-8', errors='ignore')
                if cmd_output.strip():
                    log_lines.append(cmd_output.strip())

            log_lines.append('\n[+] 所有命令已发送完毕。')
            tn.write(b'exit\n') # 发送退出命令
            
            return True, "\n".join(log_lines)

        except Exception as e:
            error_message = f"[x] Telnet 操作期间发生错误: {e}"
            log_lines.append(error_message)
            logger.error(error_message) # 同时也在后台日志中记录错误
            return False, "\n".join(log_lines)
        finally:
            if tn:
                tn.close()
                log_lines.append('[+] Telnet 连接已关闭。')

    # --- 注册指令的装饰器 ---
    # 监听 /checkwall 命令
    @filter.command("checkwall")
    async def reset_firewall_handler(self, event: AstrMessageEvent):
        """处理 /checkwall 命令，执行防火墙重置任务"""
        
        # 1. 先给用户一个即时反馈，告诉他任务已经开始
        yield event.plain_result("收到指令，正在开始执行防火墙重置任务... 这可能需要几秒钟，请稍候。")

        # 2. 使用 asyncio.to_thread 在新线程中运行阻塞的 telnet 任务
        #    这可以防止机器人主程序被卡住
        #    我们 await 等待这个线程执行完成
        success, log_message = await asyncio.to_thread(self._run_telnet_task)

        # 3. 任务完成后，根据结果向用户发送最终报告
        if success:
            final_message = "✅ 防火墙重置任务成功完成。\n\n"
        else:
            final_message = "❌ 防火墙重置任务失败。\n\n"
        
        # 附上详细的操作日志
        final_message += "--- 操作日志 ---\n" + log_message
        
        yield event.plain_result(final_message)

    # 插件的生命周期方法，这里不需要特别实现
    async def initialize(self):
        pass

    async def terminate(self):
        pass
