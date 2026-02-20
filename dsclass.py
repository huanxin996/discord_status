"""
HX Discord Status — 静态类与常量定义模块 (dsclass.py)

包含所有 Discord Gateway 协议常量、活动类型枚举、在线状态枚举、
网络地址常量以及工具函数。所有内容均为纯静态定义，无外部依赖。
"""


# ═══════════════════════════════════════════════════════════
# Gateway Opcodes
# ═══════════════════════════════════════════════════════════

class OpCode:
    """Discord Gateway Opcodes（操作码）

    参考: https://discord.com/developers/docs/events/gateway-events#receive-events
    """
    DISPATCH        = 0   # 服务端 → 客户端：事件分发（携带 t 和 s 字段）
    HEARTBEAT       = 1   # 双向：心跳包
    IDENTIFY        = 2   # 客户端 → 服务端：身份认证
    PRESENCE_UPDATE = 3   # 客户端 → 服务端：状态更新（Rich Presence）
    VOICE_STATE     = 4   # 客户端 → 服务端：语音状态更新
    RESUME          = 6   # 客户端 → 服务端：会话恢复
    RECONNECT       = 7   # 服务端 → 客户端：要求客户端重连
    REQUEST_MEMBERS = 8   # 客户端 → 服务端：请求服务器成员列表
    INVALID_SESSION = 9   # 服务端 → 客户端：会话无效
    HELLO           = 10  # 服务端 → 客户端：首次握手（携带心跳间隔）
    HEARTBEAT_ACK   = 11  # 服务端 → 客户端：心跳确认


# ═══════════════════════════════════════════════════════════
# 活动类型
# ═══════════════════════════════════════════════════════════

class ActivityType:
    """Discord 活动类型枚举

    决定用户状态显示为「正在玩 / 正在直播 / 正在听 / ...」等前缀。
    """
    PLAYING   = 0  # 正在玩（Playing XXX）         ← 最常用
    STREAMING = 1  # 正在直播（Streaming XXX）
    LISTENING = 2  # 正在听（Listening to XXX）
    WATCHING  = 3  # 正在看（Watching XXX）
    # 注意：type=4 为自定义状态（仅限客户端 UI），不可通过 Gateway 设置
    COMPETING = 5  # 正在竞技（Competing in XXX）

    # 类型名称映射（用于日志输出）
    _NAMES = {
        0: "Playing",
        1: "Streaming",
        2: "Listening",
        3: "Watching",
        5: "Competing",
    }

    @classmethod
    def name_of(cls, type_id: int) -> str:
        """根据类型 ID 返回可读名称"""
        return cls._NAMES.get(type_id, f"Unknown({type_id})")


# ═══════════════════════════════════════════════════════════
# 在线状态
# ═══════════════════════════════════════════════════════════

class StatusType:
    """Discord 在线状态枚举"""
    ONLINE    = "online"     # 在线（绿色圆点）
    IDLE      = "idle"       # 离开（黄色月亮）
    DND       = "dnd"        # 请勿打扰（红色减号）
    INVISIBLE = "invisible"  # 隐身（灰色，Rich Presence 仍对好友可见）

    # 所有有效状态值（用于校验）
    ALL = {ONLINE, IDLE, DND, INVISIBLE}


# ═══════════════════════════════════════════════════════════
# 网络常量
# ═══════════════════════════════════════════════════════════

# Discord Gateway WebSocket 地址
#   v9    : 当前稳定版本
#   json  : 使用 JSON 编码
#   zlib  : 传输层压缩（必须，否则 Identify 后会被静默断开）
GATEWAY_URL = "wss://gateway.discord.gg/?v=9&encoding=json&compress=zlib-stream"

# Discord 桌面客户端 User-Agent（模拟 Electron 客户端）
DISCORD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "discord/1.0.9163 Chrome/124.0.6367.243 "
    "Electron/30.2.0 Safari/537.36"
)

# zlib 数据流结束标记（4 字节 SYNC FLUSH）
ZLIB_SUFFIX = b"\x00\x00\xff\xff"

# Discord Remote Auth Gateway（QR 码登录用）
REMOTE_AUTH_URL = "wss://remote-auth-gateway.discord.gg/?v=2"

# Discord REST API 基础地址
DISCORD_API = "https://discord.com/api/v9"

# Discord Web 页面（用于抓取 BUILD_NUMBER）
DISCORD_APP_URL = "https://discord.com/app"


# ═══════════════════════════════════════════════════════════
# Gateway 协议常量
# ═══════════════════════════════════════════════════════════

# 默认回退 Build Number（当无法从 Discord 页面获取时使用）
DEFAULT_BUILD_NUMBER = 499123

# Gateway capabilities 位掩码
# 30717 = 0x77FD，启用 lazy guilds / guild_scheduled_events / auto_moderation 等
GATEWAY_CAPABILITIES = 30717

# 致命关闭码集合 — 收到这些码时不应重连
#   4004 = Token 无效
#   4013 = Invalid Intent
#   4014 = Disallowed Intent
FATAL_CLOSE_CODES = frozenset({4004, 4013, 4014})

# 默认心跳间隔（秒），在收到 Hello 之前使用
DEFAULT_HEARTBEAT_INTERVAL = 41.25

# 默认最大重连尝试次数（0 = 无限重连）
DEFAULT_MAX_RECONNECT_ATTEMPTS = 0


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def format_user_display(username: str, discriminator: str = "0") -> str:
    """格式化 Discord 用户显示名称

    新版 Discord 已取消 discriminator（值为 "0"），直接显示 username。
    旧版用户仍可能有 #1234 后缀。

    Args:
        username: 用户名
        discriminator: 标识符，"0" 表示新版用户

    Returns:
        格式化后的显示名，如 "username" 或 "username#1234"
    """
    if discriminator == "0":
        return username
    return f"{username}#{discriminator}"
