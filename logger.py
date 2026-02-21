"""
HX Discord Status — 日志模块 (logger.py)

提供统一的日志管理：
  - 控制台输出：可配置最低等级
  - 文件记录：按日期自动轮转，每天生成一个独立文件
  - 文件命名格式: discord_status_YYYY-MM-DD.log
  - 日志保留天数可配置，超期文件自动清理
  - 支持控制台 / 文件记录等级独立配置

无内部项目依赖，可被任何模块安全导入。
"""

import logging
import re
import sys
from datetime import date, datetime, timedelta
from logging.handlers import BaseRotatingHandler
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

# 日志根目录（项目根目录下的 log/ 文件夹）
LOG_DIR = Path(__file__).parent / "log"

# 日志文件基础名称（不含日期和扩展名）
LOG_BASE_NAME = "discord_status"

# 根 Logger 名称（所有子模块使用 "hx_discord.<module>" 格式）
ROOT_LOGGER_NAME = "hx_discord"

# ── 日志格式 ──────────────────────────────────────────────

# 文件格式：完整时间 + 等级 + 模块名 + 消息
_FILE_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"

# 控制台格式：简洁时间 + 等级 + 消息
_CONSOLE_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"

# 时间格式
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 模块级初始化标记
_initialized = False   # 是否已完成基础初始化（默认参数）
_configured = False    # 是否已使用用户自定义配置初始化（来自 config.yml）


# ═══════════════════════════════════════════════════════════
# 日期轮转 Handler
# ═══════════════════════════════════════════════════════════

class DateRotatingFileHandler(BaseRotatingHandler):
    """按日期轮转的文件日志 Handler

    - 文件命名：{base_name}_{YYYY-MM-DD}.log
    - 每天零点（首条日志触发检测）自动轮转到新文件
    - 启动时自动清理超出保留天数的历史文件
    """

    #: 日志文件名匹配模式（用于清理旧文件）
    _FILENAME_RE = re.compile(r'^(.+)_(\d{4}-\d{2}-\d{2})\.log$')

    def __init__(
        self,
        log_dir: Path,
        base_name: str,
        retention_days: int = 7,
        encoding: str = "utf-8",
    ):
        """
        Args:
            log_dir:        日志存放目录
            base_name:      文件基础名称（不含日期和 .log 后缀）
            retention_days: 保留历史日志的天数（超出则删除）
            encoding:       文件编码
        """
        self.log_dir = log_dir
        self.base_name = base_name
        self.retention_days = retention_days
        self._current_date: date = datetime.now().date()

        log_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._get_filepath(self._current_date)

        super().__init__(str(filepath), mode="a", encoding=encoding, delay=False)

        # 启动时清理超期文件
        self._cleanup_old_logs()

    # ── 文件路径 ───────────────────────────────────────

    def _get_filepath(self, log_date: date) -> Path:
        """根据日期生成日志文件路径"""
        return self.log_dir / f"{self.base_name}_{log_date.strftime('%Y-%m-%d')}.log"

    # ── 轮转判断 ───────────────────────────────────────

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """检查是否需要轮转（日期变更时返回 True）"""
        return datetime.now().date() != self._current_date

    def doRollover(self) -> None:
        """执行轮转：关闭当前文件，切换到新日期文件，清理旧文件"""
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]

        self._current_date = datetime.now().date()
        self.baseFilename = str(self._get_filepath(self._current_date))
        self.stream = self._open()

        self._cleanup_old_logs()

    # ── 旧文件清理 ─────────────────────────────────────

    def _cleanup_old_logs(self) -> None:
        """删除超出保留天数的历史日志文件

        通过文件名中的日期字段判断，只处理符合命名规则的文件。
        """
        if self.retention_days <= 0:
            return

        cutoff: date = datetime.now().date() - timedelta(days=self.retention_days)

        try:
            for f in self.log_dir.iterdir():
                if not f.is_file():
                    continue
                m = self._FILENAME_RE.match(f.name)
                if not m or m.group(1) != self.base_name:
                    continue
                try:
                    file_date = datetime.strptime(m.group(2), "%Y-%m-%d").date()
                    if file_date < cutoff:
                        f.unlink()
                except (ValueError, OSError):
                    pass
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════

def setup_logger(
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    log_retention_days: int = 7,
) -> logging.Logger:
    """初始化并配置项目根 Logger

    支持两阶段初始化（参考 logger2.py 的单例重配置模式）：
      1. 懒初始化阶段：模块导入时 get_logger() 自动以默认参数调用，
         保证早期日志不丢失。
      2. 用户配置阶段：main.py 从 config.yml 读取配置后再次调用，
         传入自定义参数，覆盖默认 Handler 设置。

    判断逻辑（与 logger2.py 的 _configured 标记一致）：
      - 已用用户配置初始化过 + 本次是默认参数 → 跳过（不重复配置）
      - 未初始化 / 本次传入自定义参数        → 执行（重新）配置

    Args:
        console_level:      控制台输出最低等级 (DEBUG / INFO / WARNING / ERROR)
        file_level:         文件记录最低等级 (DEBUG / INFO / WARNING / ERROR)
        log_retention_days: 日志文件保留天数，超出则自动删除（0 = 永久保留）

    Returns:
        配置完成的根 logging.Logger 实例
    """
    global _initialized, _configured

    logger = logging.getLogger(ROOT_LOGGER_NAME)

    # ── 判断本次调用是否携带用户自定义配置 ────────────
    is_custom = (
        console_level != "INFO"
        or file_level != "DEBUG"
        or log_retention_days != 7
    )

    # 已经用用户配置初始化过，且本次是默认参数调用 → 跳过
    if _configured and not is_custom:
        return logger

    # 已经用默认参数初始化过，且本次仍是默认参数 → 跳过
    if _initialized and not is_custom:
        return logger

    # ── 需要（重新）配置：清理旧 Handler ─────────────
    if _initialized:
        logger.handlers.clear()

    # 根 Logger 设为 DEBUG，由各 Handler 各自过滤
    logger.setLevel(logging.DEBUG)

    # ── 文件 Handler（按日期轮转） ────────────────────
    file_handler = DateRotatingFileHandler(
        log_dir=LOG_DIR,
        base_name=LOG_BASE_NAME,
        retention_days=log_retention_days,
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

    # ── 更新状态标记 ─────────────────────────────────
    _initialized = True
    if is_custom:
        _configured = True

    logger.debug(
        "日志系统%s完成 (控制台=%s, 文件=%s, 保留=%d天)",
        "重新配置" if is_custom else "初始化",
        console_level, file_level, log_retention_days,
    )

    return logger


def get_logger(name: str = "") -> logging.Logger:
    """获取子 Logger

    如果 setup_logger() 尚未被调用，会自动以默认参数执行懒初始化，
    确保在 main.py 显式调用 setup_logger() 之前，模块导入阶段产生
    的日志也能被正常记录（不会丢失）。

    后续 main.py 使用用户配置调用 setup_logger() 时，会自动清理默认
    Handler 并用新配置重建，已获取的子 Logger 无需重新获取。

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
    # 懒初始化：首次调用 get_logger() 时自动用默认参数配置
    if not _initialized:
        setup_logger()

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
        level = logging.INFO
    return level
