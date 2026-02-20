"""
HX Discord Status — 配置管理模块 (config.py)

使用 YAML 格式读取配置文件 (config.yml)，提供：
  - 类型安全的属性访问
  - 配置校验与默认值
  - 热更新检测 (has_changed / reload)
  - Token 回写（保留 YAML 注释）

依赖: pyyaml (外部), logger (本项目)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from logger import get_logger

# 模块级 Logger（继承 hx_discord 根 Logger 配置）
log = get_logger("config")

# 默认配置文件路径（项目根目录下的 config.yml）
CONFIG_PATH = Path(__file__).parent / "config.yml"


# ═══════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════

class ConfigError(Exception):
    """配置相关错误（文件缺失、格式错误、必填字段缺失等）"""
    pass


# ═══════════════════════════════════════════════════════════
# 配置管理类
# ═══════════════════════════════════════════════════════════

class AppConfig:
    """应用配置管理类

    从 YAML 文件加载配置，提供属性式访问和热更新能力。

    Usage:
        cfg = AppConfig()                   # 加载 config.yml（严格模式）
        cfg = AppConfig(strict=False)       # 跳过 Token 校验（login 时使用）
        print(cfg.game_name)                # 属性访问
        if cfg.has_changed():               # 检测文件变更
            cfg.reload()                    # 热更新
    """

    def __init__(self, path: Path | str | None = None, strict: bool = True):
        """初始化配置

        Args:
            path:   配置文件路径，默认为项目根目录下的 config.yml
            strict: 是否启用严格校验（检查 Token 是否有效）
        """
        self._path = Path(path) if path else CONFIG_PATH
        self._raw: dict = {}
        self._hash: str = ""
        self._load(strict=strict)

    # ═════════════════════════════════════════════════════
    # 属性访问 — 必填项
    # ═════════════════════════════════════════════════════

    @property
    def token(self) -> str:
        """Discord 用户 Token"""
        return self._raw.get("token", "")

    # ═════════════════════════════════════════════════════
    # 属性访问 — 活动内容
    # ═════════════════════════════════════════════════════

    @property
    def game_name(self) -> str:
        """游戏名称（显示在「正在玩 XXX」中）"""
        return self._raw.get("game_name", "Custom Game")

    @property
    def activity_type(self) -> int:
        """活动类型 (0=游玩 1=直播 2=听 3=看 5=竞技)"""
        return int(self._raw.get("activity_type", 0))

    @property
    def details(self) -> str:
        """第一行描述文字（需要 application_id）"""
        return self._raw.get("details", "")

    @property
    def state(self) -> str:
        """第二行状态文字（需要 application_id）"""
        return self._raw.get("state", "")

    # ═════════════════════════════════════════════════════
    # 属性访问 — 图标 & 富文本
    # ═════════════════════════════════════════════════════

    @property
    def application_id(self) -> str:
        """Discord Application ID（用于图标/详情/按钮）"""
        return str(self._raw.get("application_id", ""))

    @property
    def large_image_key(self) -> str:
        """大图标 asset 名称或图片 URL"""
        return self._raw.get("large_image_key", "")

    @property
    def large_image_text(self) -> str:
        """大图标鼠标悬浮提示文字"""
        return self._raw.get("large_image_text", "")

    @property
    def small_image_key(self) -> str:
        """小图标 asset 名称或图片 URL"""
        return self._raw.get("small_image_key", "")

    @property
    def small_image_text(self) -> str:
        """小图标鼠标悬浮提示文字"""
        return self._raw.get("small_image_text", "")

    # ═════════════════════════════════════════════════════
    # 属性访问 — 按钮
    # ═════════════════════════════════════════════════════

    @property
    def buttons(self) -> list[dict]:
        """可点击按钮列表 (最多 2 个)，每项含 label 和 url"""
        raw = self._raw.get("buttons", [])
        if isinstance(raw, list):
            return raw[:2]
        return []

    # ═════════════════════════════════════════════════════
    # 属性访问 — 时间显示
    # ═════════════════════════════════════════════════════

    @property
    def start_time_mode(self) -> str:
        """时间显示模式 (auto / custom / none)"""
        return self._raw.get("start_time_mode", "auto")

    @property
    def custom_elapsed_minutes(self) -> int:
        """自定义已运行分钟数（仅 custom 模式生效）"""
        return int(self._raw.get("custom_elapsed_minutes", 0))

    @property
    def auto_save_minutes(self) -> float:
        """auto 模式保存的已运行分钟数（断线恢复用）"""
        return float(self._raw.get("auto_save_minutes", 0))

    # ═════════════════════════════════════════════════════
    # 属性访问 — 在线状态
    # ═════════════════════════════════════════════════════

    @property
    def status(self) -> str:
        """在线状态 (online / idle / dnd / invisible)"""
        return self._raw.get("status", "online")

    # ═════════════════════════════════════════════════════
    # 属性访问 — 运行参数
    # ═════════════════════════════════════════════════════

    @property
    def reconnect_delay(self) -> int:
        """重连基础延迟（秒）"""
        return int(self._raw.get("reconnect_delay", 5))

    @property
    def max_reconnect_attempts(self) -> int:
        """最大重连尝试次数（0 = 无限重连）"""
        return int(self._raw.get("max_reconnect_attempts", 0))

    @property
    def config_reload_interval(self) -> int:
        """配置热更新检查间隔（秒），最小 15 秒"""
        return max(int(self._raw.get("config_reload_interval", 60)), 15)

    # ═════════════════════════════════════════════════════
    # 属性访问 — 日志设置
    # ═════════════════════════════════════════════════════

    @property
    def console_level(self) -> str:
        """控制台日志输出等级"""
        log_cfg = self._raw.get("logging", {})
        if isinstance(log_cfg, dict):
            return log_cfg.get("console_level", "INFO")
        return "INFO"

    @property
    def file_level(self) -> str:
        """文件日志记录等级"""
        log_cfg = self._raw.get("logging", {})
        if isinstance(log_cfg, dict):
            return log_cfg.get("file_level", "DEBUG")
        return "DEBUG"

    @property
    def log_retention_days(self) -> int:
        """日志文件保留天数（超期自动删除，0 = 永久保留）"""
        log_cfg = self._raw.get("logging", {})
        if isinstance(log_cfg, dict):
            return int(log_cfg.get("log_retention_days", 7))
        return 7

    # ═════════════════════════════════════════════════════
    # 核心方法
    # ═════════════════════════════════════════════════════

    def _load(self, strict: bool = True) -> None:
        """从 YAML 文件加载配置

        Args:
            strict: 是否校验 Token

        Raises:
            ConfigError: 文件缺失、格式错误或 Token 缺失
        """
        if not self._path.exists():
            raise ConfigError(f"找不到配置文件: {self._path}")

        with open(self._path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        try:
            self._raw = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"config.yml 格式错误: {e}")

        # 类型保护：确保解析结果是字典
        if not isinstance(self._raw, dict):
            raise ConfigError("config.yml 顶层结构必须是映射（字典）")

        # 严格模式下校验 Token
        if strict:
            token = self.token
            if not token or token == "你的Token":
                raise ConfigError(
                    "请先在 config.yml 中填入你的 Discord Token\n"
                    "  运行 python login.py 可自动获取并写入"
                )

        # 计算内容哈希（用于热更新检测）
        self._hash = json.dumps(self._raw, sort_keys=True, ensure_ascii=False)

    def has_changed(self) -> bool:
        """检测配置文件内容是否有变更

        通过比较序列化哈希值判断，不会修改当前配置。

        Returns:
            True 表示文件内容与内存中不一致
        """
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                new_raw = yaml.safe_load(f.read()) or {}
            new_hash = json.dumps(new_raw, sort_keys=True, ensure_ascii=False)
            return new_hash != self._hash
        except Exception:
            return False

    def reload(self) -> bool:
        """重新加载配置文件

        Returns:
            True 表示重载成功
        """
        try:
            self._load(strict=True)
            log.info("配置文件重载成功")
            return True
        except ConfigError as e:
            log.warning("配置重载失败: %s", e)
            return False

    def save_auto_minutes(self, minutes: float) -> None:
        """将 auto 模式已运行分钟数保存到 YAML 文件

        使用正则替换 auto_save_minutes 行，保留注释和其他内容。

        Args:
            minutes: 已运行分钟数（保留 1 位小数）
        """
        minutes = round(minutes, 1)
        if not self._path.exists():
            return

        content = self._path.read_text(encoding="utf-8")
        if re.search(r"^auto_save_minutes\s*:", content, flags=re.MULTILINE):
            new_content = re.sub(
                r'^(auto_save_minutes\s*:\s*).*$',
                lambda m: f'{m.group(1)}{minutes}',
                content,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            # 字段不存在，在 custom_elapsed_minutes 后面插入
            if re.search(r"^custom_elapsed_minutes\s*:", content, flags=re.MULTILINE):
                new_content = re.sub(
                    r'^(custom_elapsed_minutes\s*:.*)$',
                    lambda m: f'{m.group(1)}\nauto_save_minutes: {minutes}',
                    content,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                new_content = content + f'\nauto_save_minutes: {minutes}\n'

        self._path.write_text(new_content, encoding="utf-8")
        self._raw["auto_save_minutes"] = minutes
        # 更新哈希以避免热更新误触发
        self._hash = json.dumps(self._raw, sort_keys=True, ensure_ascii=False)
        log.debug("auto_save_minutes 已保存: %.1f", minutes)

    def update_token(self, token: str) -> None:
        """更新 Token 并写回 YAML 文件（保留注释）

        使用正则替换 token 行，不会影响文件中的注释和其他内容。

        Args:
            token: 新的 Discord 用户 Token
        """
        if self._path.exists():
            content = self._path.read_text(encoding="utf-8")
            # 匹配 token: "xxx" 或 token: xxx 或 token: 'xxx' 格式
            if re.search(r"^token\s*:", content, flags=re.MULTILINE):
                new_content = re.sub(
                    r'^(token\s*:\s*).*$',
                    f'\\1"{token}"',
                    content,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                # 文件中无 token 字段，追加到开头
                new_content = f'token: "{token}"\n{content}'
        else:
            # 文件不存在，创建最小配置
            new_content = (
                "# HX Discord Status 配置\n"
                f'token: "{token}"\n'
                'game_name: "Custom Game"\n'
            )

        self._path.write_text(new_content, encoding="utf-8")
        log.info("Token 已写入 %s", self._path.name)

    # ═════════════════════════════════════════════════════
    # 通用访问
    # ═════════════════════════════════════════════════════

    def get(self, key: str, default: Any = None) -> Any:
        """字典式键值访问"""
        return self._raw.get(key, default)

    def to_dict(self) -> dict:
        """返回原始配置字典的浅拷贝"""
        return dict(self._raw)

    def __repr__(self) -> str:
        return (
            f"AppConfig(path={self._path}, "
            f"game={self.game_name!r}, "
            f"status={self.status!r})"
        )
