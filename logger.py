"""
HX Discord Status — 日志模块 (logger.py)

提供统一的日志管理：
  - 控制台输出：彩色分级，可配置最低等级
  - 文件记录：自适应大小轮转，保存在 log/ 目录下
  - 支持自定义控制台 / 文件记录等级分离
  - 使用 RotatingFileHandler 实现自动轮转

无内部项目依赖，可被任何模块安全导入。
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

# 日志根目录（项目根目录下的 log/ 文件夹）
LOG_DIR = Path(__file__).parent / "log"

# 日志文件名
LOG_FILENAME = "discord_status.log"

# 根 Logger 名称（所有子模块使用 "hx_discord.<module>" 格式）
ROOT_LOGGER_NAME = "hx_discord"

# ── 日志格式 ──────────────────────────────────────────────

# 文件格式：完整时间 + 等级 + 模块名 + 消息
_FILE_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"

# 控制台格式：简洁时间 + 等级 + 消息
_CONSOLE_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"

# 时间格式
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 模块级初始化标记，防止多次调用 setup_logger
_initialized = False


# ═══════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════

def setup_logger(
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    max_file_size_mb: float = 5.0,
    backup_count: int = 5,
) -> logging.Logger:
    """初始化并配置项目根 Logger

    应在程序启动时调用一次。后续通过 get_logger() 获取子 Logger。

    Args:
        console_level:   控制台输出最低等级 (DEBUG / INFO / WARNING / ERROR)
        file_level:      文件记录最低等级 (DEBUG / INFO / WARNING / ERROR)
        max_file_size_mb: 单个日志文件最大大小（MB），超过自动轮转
        backup_count:    保留的历史日志文件数量

    Returns:
        配置完成的根 logging.Logger 实例
    """
    global _initialized

    logger = logging.getLogger(ROOT_LOGGER_NAME)

    # 防止重复添加 handler
    if _initialized:
        return logger

    # 根 Logger 设为 DEBUG，由各 Handler 各自过滤
    logger.setLevel(logging.DEBUG)

    # ── 创建日志目录 ──────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── 文件 Handler（自适应大小轮转） ────────────────
    log_file = LOG_DIR / LOG_FILENAME
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(_parse_level(file_level))
    file_handler.setFormatter(
        logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT)
    )
    logger.addHandler(file_handler)

    # ── 控制台 Handler ────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(_parse_level(console_level))
    console_handler.setFormatter(
        logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT)
    )
    logger.addHandler(console_handler)

    _initialized = True
    logger.debug("日志系统初始化完成 (控制台=%s, 文件=%s, 轮转=%.1fMB×%d)",
                 console_level, file_level, max_file_size_mb, backup_count)

    return logger


def get_logger(name: str = "") -> logging.Logger:
    """获取子 Logger

    Args:
        name: 子模块名称（如 "gateway"、"login"、"config"）
              为空则返回根 Logger

    Returns:
        logging.Logger 实例，继承根 Logger 的 Handler 配置

    Usage:
        log = get_logger("gateway")
        log.info("连接成功")
        # 输出: [2026-02-20 12:00:00] [INFO] [hx_discord.gateway] 连接成功
    """
    if name:
        return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
    return logging.getLogger(ROOT_LOGGER_NAME)


# ═══════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════

def _parse_level(level_str: str) -> int:
    """将等级字符串转为 logging 等级常量

    Args:
        level_str: 等级名称字符串 (DEBUG/INFO/WARNING/ERROR/CRITICAL)

    Returns:
        logging 模块的等级整数值
    """
    level = getattr(logging, level_str.upper(), None)
    if level is None:
        # 回退到 INFO
        level = logging.INFO
    return level
