"""
HX Discord Status — 主入口 (main.py)

程序启动流程：
  1. 加载配置文件 (config.yml)
  2. 初始化日志系统
  3. 获取 Discord Build Number
  4. 启动 Gateway 客户端

支持 --login 参数进入登录模式获取 Token。

用法:
  python main.py            # 正常启动（需要已配置 Token）
  python main.py --login    # 进入登录模式获取 Token
"""

import asyncio
import signal
import sys

from config import AppConfig, ConfigError
from logger import setup_logger, get_logger
from discord import GatewayClient, fetch_build_number


def main() -> None:
    """程序主入口"""

    # ── 检查是否为登录模式 ────────────────────────────
    if "--login" in sys.argv:
        # 初始化基础日志（使用默认设置）
        setup_logger(console_level="INFO", file_level="DEBUG")
        from login import run_login
        run_login()
        return

    # ── 1. 加载配置 ──────────────────────────────────
    try:
        config = AppConfig()
    except ConfigError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    # ── 2. 初始化日志系统 ─────────────────────────────
    setup_logger(
        console_level=config.console_level,
        file_level=config.file_level,
        log_retention_days=config.log_retention_days,
    )
    log = get_logger("main")

    # ── 3. 启动信息 ──────────────────────────────────
    print("=" * 50)
    print("  HX Discord Status  (Gateway / 模块化版)")
    print("=" * 50)
    print(f"  游戏名称 : {config.game_name}")
    print(f"  详情     : {config.details or '-'}")
    print(f"  状态文字 : {config.state or '-'}")
    print(f"  在线状态 : {config.status}")
    print(f"  时间模式 : {config.start_time_mode}")
    print("-" * 50)

    # ── 4. 获取 Build Number ─────────────────────────
    log.info("正在获取最新 Discord Build Number ...")
    build_num = fetch_build_number()
    print()

    # ── 5. 创建并启动 Gateway 客户端 ─────────────────
    client = GatewayClient(config, build_num)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 优雅退出处理
    def _stop():
        client.stop()
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # Unix 信号处理（Windows 不支持 add_signal_handler）
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _stop)
        loop.add_signal_handler(signal.SIGTERM, _stop)

    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        log.info("收到退出信号 (Ctrl+C)")
        _stop()
    finally:
        # 清理残留异步任务
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        loop.close()

    log.info("程序已退出")


if __name__ == "__main__":
    main()
