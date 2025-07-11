# -*- coding: utf-8 -*-
# 文件名: firewall_resetter.py

# 导入 AstrBot 框架的必要模块
# 注意，我们需要从 filter 中导入 PermissionType
from astrbot.api.event import filter, AstrMessageEvent, PermissionType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 导入我们需要的标准库
import telnetlib
import time
import asyncio

# --- 插件注册信息 ---
@register(
    "firewall_resetter",           # 插件的唯一ID
    "YourName",                    # 你的名字
    "一个通过Telnet重置防火墙的插件 (仅管理员可用)", # 插件描述
    "1.0.0"                        # 插件版本
)
class FirewallResetPlugin(Star):
    """
    这个插件监听 /checkwall 命令，并通过 Telnet 连接到指定设备执行一系列命令来重置防火墙。
    该命令仅限管理员使用。
    """
    def __init__(self, context: Context):
        super().__init__(context)

        # --- 将配置信息放在插件类内部，方便管理 ---
        self.TELNET_CONFIG = {
            "host": "192.168.1.1",
            "port": 23,
            "username": "admin",
            "password": "admin",
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
    def _run_telnet_task(self) -> tuple[bool, str]:
        # ... 这部分代码保持不变 ...
        log_lines = []
        host = self.TELNET_CONFIG["host"]
        port = self.TELNET_CONFIG["port"]
        username = self.TELNET_CONFIG["username"]
        password = self.TELNET_CONFIG["password"]
        log_lines.append(f"[*] 准备通过 Telnet 连接到 {host}:{port}...")
        tn = None
        try:
            tn = telnetlib.Telnet(host, port, timeout=10)
            log_lines.append("[+] 连接成功。")
            time.sleep(self.DELAYS["afterConnect"])
            log_lines.append(f"[>] 发送用户名: {username}")
            tn.write(f"{username}\n".encode('ascii'))
            time.sleep(self.DELAYS["afterUsername"])
            log_lines.append("[>] 发送密码...")
            tn.write(f"{password}\n".encode('ascii'))
            time.sleep(self.DELAYS["afterPassword"])
            login_output = tn.read_very_eager().decode('utf-8', errors='ignore')
            if login_output:
                log_lines.append('--- 登录后设备输出 ---\n' + login_output.strip() + '\n--- 输出结束 ---')
            log_lines.append('[+] 开始按顺序执行命令...')
            for cmd in self.COMMANDS_TO_RUN:
                log_lines.append(f"    -> 正在执行: {cmd}")
                tn.write(f"{cmd}\n".encode('ascii'))
                time.sleep(self.DELAYS["betweenCommands"])
                cmd_output = tn.read_very_eager().decode('utf-8', errors='ignore')
                if cmd_output.strip():
                    log_lines.append(cmd_output.strip())
            log_lines.append('\n[+] 所有命令已发送完毕。')
            tn.write(b'exit\n')
            return True, "\n".join(log_lines)
        except Exception as e:
            error_message = f"[x] Telnet 操作期间发生错误: {e}"
            log_lines.append(error_message)
            logger.error(error_message)
            return False, "\n".join(log_lines)
        finally:
            if tn:
                tn.close()
                log_lines.append('[+] Telnet 连接已关闭。')


    # --- 注册指令的装饰器 ---
    # 监听 /checkwall 命令
    @filter.command("checkwall")
    # 【新增】添加权限过滤器，只允许管理员(ADMIN)或更高级别的人使用
    @filter.permission_type(PermissionType.ADMIN)
    async def reset_firewall_handler(self, event: AstrMessageEvent):
        """处理 /checkwall 命令，执行防火墙重置任务"""
        
        # 1. 先给用户一个即时反馈
        yield event.plain_result("权限验证通过。正在开始执行防火墙重置任务...")

        # 2. 在新线程中运行阻塞任务
        success, log_message = await asyncio.to_thread(self._run_telnet_task)

        # 3. 任务完成后，发送最终报告
        if success:
            final_message = "✅ 防火墙重置任务成功完成。\n\n"
        else:
            final_message = "❌ 防火墙重置任务失败。\n\n"
        
        final_message += "--- 操作日志 ---\n" + log_message
        
        yield event.plain_result(final_message)

    # 插件的生命周期方法
    async def initialize(self):
        logger.info("防火墙重置插件已加载，/checkwall 命令仅对管理员开放。")

    async def terminate(self):
        pass
