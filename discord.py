"""
HX Discord Status — Gateway 连接模块 (discord.py)

负责与 Discord Gateway WebSocket 的全部交互：
  - 连接建立与 zlib-stream 解压
  - 身份认证 (Identify) 与会话恢复 (Resume)
  - 心跳维持 (Heartbeat)
  - Rich Presence 状态设置与更新
  - 配置热更新监听
  - 断线自动重连（指数退避）

注意: 文件名 discord.py 会遮蔽 discord.py 库，
      但本项目不使用该库，仅使用 websockets。

依赖: websockets (外部), dsclass / config / logger (本项目)
"""

import asyncio
import json
import random
import re
import time
import urllib.request
import zlib

import websockets
import websockets.exceptions

from dsclass import (
    OpCode,
    ActivityType,
    GATEWAY_URL,
    DISCORD_UA,
    DISCORD_APP_URL,
    ZLIB_SUFFIX,
    DEFAULT_BUILD_NUMBER,
    GATEWAY_CAPABILITIES,
    FATAL_CLOSE_CODES,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    format_user_display,
)
from config import AppConfig
from logger import get_logger

log = get_logger("gateway")


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def fetch_build_number() -> int:
    """从 Discord Web 页面自动获取最新 client_build_number

    建立连接时需要发送正确的 build_number，否则会被静默断开。
    此函数请求 Discord App 页面，从 window.GLOBAL_ENV 中提取。

    Returns:
        最新的 build number 整数值；失败时返回默认值
    """
    try:
        req = urllib.request.Request(
            DISCORD_APP_URL,
            headers={"User-Agent": DISCORD_UA},
        )
        log.debug("请求 Discord App 页面获取 Build Number: %s", DISCORD_APP_URL)
        html = urllib.request.urlopen(req, timeout=10).read().decode()
        m = re.search(r'"BUILD_NUMBER"\s*:\s*"(\d+)"', html)
        if m:
            build_num = int(m.group(1))
            log.info("获取到 Build Number: %d", build_num)
            return build_num
        log.warning("页面中未找到 BUILD_NUMBER 字段")
    except Exception as e:
        log.warning("获取 Build Number 失败: %s, 使用默认值 %d", e, DEFAULT_BUILD_NUMBER)

    return DEFAULT_BUILD_NUMBER


# ═══════════════════════════════════════════════════════════
# 资产管理
# ═══════════════════════════════════════════════════════════

def fetch_app_assets(app_id: str) -> dict:
    """从 Discord API 获取 Application 的 Rich Presence 资产列表

    Discord Gateway Activity 中 large_image/small_image 字段要求填写
    资产的雪花 ID，而非资产名称。本函数返回 名称→ID 的映射字典。

    API 端点 (无需鉴权):
        GET https://discord.com/api/v10/oauth2/applications/{app_id}/assets

    Args:
        app_id: Discord Application ID

    Returns:
        {资产名称: 资产ID字符串} 的字典；请求失败时返回空字典
    """
    if not app_id:
        log.debug("未配置 application_id，跳过资产获取")
        return {}
    url = f"https://discord.com/api/v10/oauth2/applications/{app_id}/assets"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": DISCORD_UA},
        )
        log.debug("获取应用资产列表: %s", url)
        data = urllib.request.urlopen(req, timeout=10).read()
        assets = json.loads(data)
        mapping = {a["name"]: a["id"] for a in assets if "name" in a and "id" in a}
        log.info("获取到 %d 个资产: %s", len(mapping), list(mapping.keys()))
        return mapping
    except Exception as e:
        log.warning("获取资产列表失败: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════
# Activity / Presence 构建
# ═══════════════════════════════════════════════════════════

def build_activity(config: AppConfig, start_ts: float,
                   asset_map: dict | None = None) -> dict:
    """根据配置构建 Discord Activity 对象

    Args:
        config: 应用配置实例
        start_ts: 启动时间戳 (time.time())

    Returns:
        Activity 字典，可直接嵌入 Gateway Presence 载荷
    """
    activity: dict = {
        "name": config.game_name,
        "type": config.activity_type,
    }
    log.debug("构建 Activity: name=%r, type=%d", config.game_name, config.activity_type)

    # 有 application_id 时启用富文本功能
    app_id = config.application_id
    if app_id and app_id != "你的ApplicationID":
        activity["application_id"] = app_id

        # ── 文字内容 ──────────────────────────────────
        if config.details:
            activity["details"] = config.details
        if config.state:
            activity["state"] = config.state

        # ── 图标 ──────────────────────────────────────
        # Discord Gateway 要求 large_image/small_image 填写资产的雪花 ID
        # 若传入了 asset_map（名称→ID），则自动将名称转换为 ID
        def resolve_image(key: str) -> str:
            """将资产名称解析为 ID；若已是纯数字或无映射则原样返回"""
            if not key:
                return key
            if asset_map and key in asset_map:
                return asset_map[key]
            # 如果 key 不是纯数字且没有找到映射，输出一次警告
            if asset_map is not None and not key.isdigit() and not key.startswith("mp:"):
                log.warning("资产 '%s' 未在资产列表中找到，将原样发送（可能无法显示图标）", key)
            return key

        assets = {}
        if config.large_image_key:
            assets["large_image"] = resolve_image(config.large_image_key)
        if config.large_image_text:
            assets["large_text"] = config.large_image_text
        if config.small_image_key:
            assets["small_image"] = resolve_image(config.small_image_key)
        if config.small_image_text:
            assets["small_text"] = config.small_image_text
        if assets:
            activity["assets"] = assets

        # ── 按钮 ──────────────────────────────────────
        buttons = config.buttons
        if buttons:
            activity["buttons"] = [b["label"] for b in buttons]
            activity["metadata"] = {"button_urls": [b["url"] for b in buttons]}

    # ── 时间戳 ────────────────────────────────────────
    mode = config.start_time_mode
    if mode == "auto":
        activity["timestamps"] = {"start": int(start_ts * 1000)}
        log.debug("时间戳模式: auto, start_ts=%d", int(start_ts))
    elif mode == "custom":
        fake_start = time.time() - (config.custom_elapsed_minutes * 60)
        activity["timestamps"] = {"start": int(fake_start * 1000)}
        log.debug("时间戳模式: custom, 自定义已运行 %d 分钟", config.custom_elapsed_minutes)
    else:
        log.debug("时间戳模式: none, 不附加时间戳")
    # mode == "none" → 不附加时间戳

    return activity


def build_presence_payload(config: AppConfig, start_ts: float,
                           asset_map: dict | None = None) -> dict:
    """构建完整的 Presence Update 载荷 (Opcode 3)

    Args:
        config: 应用配置
        start_ts: 启动时间戳
        asset_map: 资产名称→ID 映射（由 fetch_app_assets 获取）

    Returns:
        可直接发送的 Gateway 载荷字典
    """
    activity = build_activity(config, start_ts, asset_map)
    log.debug("构建 Presence 载荷: status=%s, activities=%d个",
             config.status, 1)
    return {
        "op": OpCode.PRESENCE_UPDATE,
        "d": {
            "since": 0,
            "activities": [activity],
            "status": config.status,
            "afk": False,
        },
    }


# ═══════════════════════════════════════════════════════════
# Gateway 客户端
# ═══════════════════════════════════════════════════════════

class GatewayClient:
    """Discord Gateway WebSocket 客户端

    最小化实现，仅维持心跳连接和 Rich Presence 状态设置。
    支持自动重连、会话恢复、配置热更新。

    Attributes:
        config: 应用配置实例
        build_number: Discord 客户端构建号
        start_ts: 启动时间戳
    """

    def __init__(self, config: AppConfig, build_number: int):
        """初始化客户端

        Args:
            config: 应用配置实例
            build_number: 从 Discord Web 获取的最新构建号
        """
        self.config = config
        self.build_number = build_number

        log.debug("初始化 GatewayClient: build_number=%d", build_number)

        # ── 连接状态 ──────────────────────────────────
        self._ws = None                   # WebSocket 连接对象
        self._inflator = None             # zlib 解压器（每次连接重建）
        self._running = True              # 运行标记

        # ── 时间管理 ──────────────────────────────────
        # 如果 auto 模式有保存过的运行时间，从保存值恢复
        saved_minutes = config.auto_save_minutes
        if config.start_time_mode == "auto" and saved_minutes > 0:
            self.start_ts = time.time() - (saved_minutes * 60)
            log.info("从保存值恢复计时: 已运行 %.1f 分钟", saved_minutes)
        else:
            self.start_ts = time.time()

        # ── 重连管理 ──────────────────────────────────
        self._reconnect_count = 0         # 当前连续重连次数
        self._max_reconnect = config.max_reconnect_attempts  # 最大重连次数 (0=无限)
        # ── 资产映射缓存 ──────────────────────────
        # Discord Gateway 要求图标填写资产雪花 ID，而非资产名称
        # 运行时通过 /oauth2/applications/{id}/assets API 自动获取
        self._asset_map: dict = {}        # {asset_name: asset_id}
        # ── Gateway 会话状态 ──────────────────────────
        self._heartbeat_interval = 41.25  # 心跳间隔（秒）
        self._sequence = None             # 最新序列号
        self._session_id = None           # 会话 ID（Resume 用）
        self._resume_url = None           # Resume 专用 URL
        self._heartbeat_acked = True      # 上次心跳是否已 ACK

    # ═════════════════════════════════════════════════════
    # 主循环
    # ═════════════════════════════════════════════════════

    async def run(self) -> None:
        """主运行循环：连接 → 会话 → 断线重连

        支持重连尝试计数，超过最大次数后停止。
        每次成功连接会重置计数器。
        调用 stop() 可优雅终止循环。
        """
        delay = self.config.reconnect_delay

        while self._running:
            try:
                # 优先使用 resume_url，否则使用默认网关地址
                url = self._resume_url or GATEWAY_URL
                log.debug("正在连接 Gateway: %s", url)
                async with websockets.connect(
                    url,
                    max_size=2 ** 20,
                    close_timeout=10,
                    ping_interval=None,
                ) as ws:
                    self._ws = ws
                    self._inflator = zlib.decompressobj()
                    delay = self.config.reconnect_delay  # 成功连接后重置退避
                    self._reconnect_count = 0            # 成功连接重置计数器
                    log.info("WebSocket 连接已建立")
                    await self._session()

            except asyncio.CancelledError:
                break

            except websockets.exceptions.ConnectionClosedError as e:
                code = e.rcvd.code if e.rcvd else "?"
                reason = e.rcvd.reason if e.rcvd else ""
                log.warning("连接被关闭 code=%s reason=%r", code, reason)
                # 致命错误不重连
                if isinstance(code, int) and code in FATAL_CLOSE_CODES:
                    log.error("致命错误 (code=%s)，停止重连。请检查 Token", code)
                    self._running = False
                    break

            except websockets.exceptions.ConnectionClosedOK:
                log.info("连接正常关闭")

            except Exception as e:
                log.warning("连接断开: %s: %s", type(e).__name__, e)

            if not self._running:
                break

            # ── 断线时保存 auto 模式运行时间 ──────────
            self._save_elapsed_time()

            # ── 重连尝试计数 ──────────────────────────
            self._reconnect_count += 1
            if self._max_reconnect > 0 and self._reconnect_count > self._max_reconnect:
                log.error("已达最大重连次数 (%d)，停止重连", self._max_reconnect)
                self._running = False
                break

            # 指数退避重连
            jitter = random.uniform(0, delay)
            wait = delay + jitter
            log.info("第 %d 次重连，%.1f 秒后尝试 ...",
                     self._reconnect_count, wait)
            await asyncio.sleep(wait)
            delay = min(delay * 1.5, 120)  # 上限 2 分钟

    # ═════════════════════════════════════════════════════
    # 会话管理
    # ═════════════════════════════════════════════════════

    async def _session(self) -> None:
        """单次 Gateway 会话的完整生命周期

        流程: Hello → Identify/Resume → 心跳 + 监听 + 热更新
        """        # ── 0. 刷新资产映射 ───────────────────────────
        await self._refresh_assets()
        # ── 1. 接收 Hello ─────────────────────────────
        msg = await self._recv()
        if msg is None:
            log.error("等待 Hello 帧时连接已关闭")
            return
        if msg.get("op") != OpCode.HELLO:
            log.error("期望 Hello(op=10)，收到 op=%s", msg.get("op"))
            return

        self._heartbeat_interval = msg["d"]["heartbeat_interval"] / 1000
        self._heartbeat_acked = True
        log.info("心跳间隔: %.2fs", self._heartbeat_interval)

        # ── 2. Identify 或 Resume ─────────────────────
        if self._session_id and self._sequence is not None:
            log.debug("存在历史会话，尝试 Resume")
            await self._send_resume()
        else:
            log.debug("无历史会话，发送 Identify")
            await self._send_identify()

        # ── 3. 启动后台任务 ───────────────────────────
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        reload_task = asyncio.create_task(self._config_reload_loop())

        try:
            await self._listen()
        except websockets.exceptions.ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else "?"
            reason = e.rcvd.reason if e.rcvd else ""
            log.warning("会话关闭 code=%s reason=%r", code, reason)
            if isinstance(code, int) and code in FATAL_CLOSE_CODES:
                log.error("致命错误，停止重连")
                self._running = False
        finally:
            # 清理后台任务
            heartbeat_task.cancel()
            reload_task.cancel()
            for task in (heartbeat_task, reload_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ═════════════════════════════════════════════════════
    # 消息监听
    # ═════════════════════════════════════════════════════

    async def _listen(self) -> None:
        """监听并处理 Gateway 消息

        处理的 Opcode:
          - DISPATCH (0): READY / RESUMED 等事件
          - HEARTBEAT (1): 服务端要求立即心跳
          - HEARTBEAT_ACK (11): 心跳确认
          - RECONNECT (7): 服务端要求重连
          - INVALID_SESSION (9): 会话失效
        """
        async for raw in self._ws:
            msg = self._decode_message(raw)
            if msg is None:
                continue

            op = msg.get("op")
            seq = msg.get("s")

            # 更新序列号
            if seq is not None:
                self._sequence = seq

            # ── 事件分发 (op=0) ───────────────────────
            if op == OpCode.DISPATCH:                
                event = msg.get("t", "UNKNOWN")
                log.debug("收到 DISPATCH 事件: %s (seq=%s)", event, seq)                
                await self._handle_dispatch(msg)

            # ── 服务端要求心跳 (op=1) ─────────────────
            elif op == OpCode.HEARTBEAT:
                log.debug("收到服务端要求心跳")
                await self._send_heartbeat()

            # ── 心跳确认 (op=11) ──────────────────
            elif op == OpCode.HEARTBEAT_ACK:
                log.debug("心跳已确认 (ACK)")
                self._heartbeat_acked = True

            # ── 服务端要求重连 (op=7) ─────────────────
            elif op == OpCode.RECONNECT:
                log.info("服务器要求重连")
                await self._ws.close(4000)
                return

            # ── 会话失效 (op=9) ───────────────────────
            elif op == OpCode.INVALID_SESSION:
                resumable = msg.get("d", False)
                if not resumable:
                    # 会话不可恢复，清空状态
                    self._session_id = None
                    self._sequence = None
                    self._resume_url = None
                log.warning("Session 失效 (可恢复=%s)，等待后重连", resumable)
                await asyncio.sleep(random.uniform(1, 5))
                await self._ws.close(4000)
                return

    async def _handle_dispatch(self, msg: dict) -> None:
        """处理 DISPATCH 事件

        Args:
            msg: 完整的 Gateway 消息字典
        """
        event = msg.get("t")

        if event == "READY":
            d = msg["d"]
            self._session_id = d.get("session_id")
            self._resume_url = d.get("resume_gateway_url")
            user = d.get("user", {})
            name = format_user_display(
                user.get("username", "?"),
                user.get("discriminator", "0"),
            )
            log.info("已连接账号: %s (ID: %s)", name, user.get("id", "?"))
            # 连接成功后立即设置 Presence
            await self._update_presence()

        elif event == "RESUMED":
            log.info("会话已恢复")
            await self._update_presence()

        # 其他 DISPATCH 事件不处理

    # ═════════════════════════════════════════════════════
    # 消息收发
    # ═════════════════════════════════════════════════════

    def _decode_message(self, raw) -> dict | None:
        """解码 Gateway 消息（处理 zlib 压缩）

        Args:
            raw: 原始 WebSocket 消息 (bytes 或 str)

        Returns:
            解码后的字典，不完整帧返回 None
        """
        if isinstance(raw, bytes):
            buf = self._inflator.decompress(raw)
            if raw[-4:] != ZLIB_SUFFIX:
                log.debug("收到不完整 zlib 帧 (%d 字节)，等待后续数据", len(raw))
                return None  # 不完整帧，等待后续数据
            return json.loads(buf.decode("utf-8"))
        return json.loads(raw)

    async def _send(self, payload: dict) -> None:
        """发送 JSON 载荷到 Gateway

        Args:
            payload: 要发送的字典
        """
        log.debug("发送 Gateway 消息: op=%s", payload.get("op"))
        await self._ws.send(json.dumps(payload))

    async def _recv(self) -> dict | None:
        """接收并解码单条 Gateway 消息

        Returns:
            消息字典，接收失败返回 None
        """
        try:
            raw = await self._ws.recv()
            msg = self._decode_message(raw)
            if msg:
                log.debug("接收 Gateway 消息: op=%s", msg.get("op"))
            return msg
        except Exception as e:
            log.debug("接收消息异常: %s", e)
            return None

    # ═════════════════════════════════════════════════════
    # 协议指令
    # ═════════════════════════════════════════════════════

    async def _refresh_assets(self) -> None:
        """获取并缓存应用资产列表（名称→ID 映射）

        Discord Gateway 要求 large_image/small_image 填写资产ID，
        此方法通过 API 自动把配置中的资产名称转换为 ID。
        """
        app_id = self.config.application_id
        if not app_id or app_id == "你的ApplicationID":
            log.debug("未配置 application_id，跳过资产刷新")
            return
        log.debug("开始获取应用资产: app_id=%s", app_id)
        mapping = await asyncio.to_thread(fetch_app_assets, app_id)
        if mapping:
            self._asset_map = mapping
            log.debug("资产映射已缓存: %d 个资产", len(mapping))
        elif not self._asset_map:
            log.warning("资产列表为空，将原样使用配置中的字符串作为图标键（可能无法显示图标）")

    async def _send_identify(self) -> None:
        """发送 Identify 载荷 (Opcode 2)

        包含 Token、capabilities、客户端属性、
        初始 Presence 和 client_state。
        """
        log.info("发送 Identify ...")
        activity = build_activity(self.config, self.start_ts, self._asset_map)

        payload = {
            "op": OpCode.IDENTIFY,
            "d": {
                "token": self.config.token,
                "capabilities": GATEWAY_CAPABILITIES,
                "properties": {
                    "os": "Windows",
                    "browser": "Discord Client",
                    "device": "",
                    "system_locale": "zh-CN",
                    "browser_user_agent": DISCORD_UA,
                    "browser_version": "30.2.0",
                    "os_version": "10.0.22631",
                    "referrer": "",
                    "referring_domain": "",
                    "referrer_current": "",
                    "referring_domain_current": "",
                    "release_channel": "stable",
                    "client_build_number": self.build_number,
                    "client_event_source": None,
                    "design_id": 0,
                },
                "presence": {
                    "activities": [activity],
                    "status": self.config.status,
                    "since": 0,
                    "afk": False,
                },
                "compress": False,  # 传输压缩由 URL 参数控制
                "client_state": {
                    "guild_versions": {},
                    "highest_last_message_id": "0",
                    "read_state_version": 0,
                    "user_guild_settings_version": -1,
                    "user_settings_version": -1,
                    "private_channels_version": "0",
                    "api_code_version": 0,
                },
            },
        }
        await self._send(payload)

    async def _send_resume(self) -> None:
        """发送 Resume 载荷 (Opcode 6)

        使用之前保存的 session_id 和 sequence 恢复会话。
        """
        log.info("发送 Resume (session=%s, seq=%s) ...",
                 self._session_id, self._sequence)
        payload = {
            "op": OpCode.RESUME,
            "d": {
                "token": self.config.token,
                "session_id": self._session_id,
                "seq": self._sequence,
            },
        }
        await self._send(payload)

    async def _send_heartbeat(self) -> None:
        """发送心跳包 (Opcode 1)"""
        log.debug("发送心跳包 (seq=%s)", self._sequence)
        await self._send({"op": OpCode.HEARTBEAT, "d": self._sequence})

    async def _update_presence(self) -> None:
        """发送 Presence Update (Opcode 3)

        根据当前配置构建并发送状态更新。
        """
        payload = build_presence_payload(self.config, self.start_ts, self._asset_map)
        await self._send(payload)
        await self._send(payload)
        log.info("状态已更新: %s | %s — %s",
                 self.config.game_name,
                 self.config.details or "-",
                 self.config.state or "-")

    # ═════════════════════════════════════════════════════
    # 后台任务
    # ═════════════════════════════════════════════════════

    async def _heartbeat_loop(self) -> None:
        """心跳维持循环

        首次发送前有随机抖动（避免多客户端同步心跳）。
        如果心跳未被 ACK，则认为连接断开并主动关闭。
        """
        # 首次心跳添加随机抖动
        jitter = self._heartbeat_interval * random.random()
        log.debug("心跳循环启动，首次抖动: %.2fs, 间隔: %.2fs", jitter, self._heartbeat_interval)
        await asyncio.sleep(jitter)

        while True:
            if not self._heartbeat_acked:
                log.warning("心跳未应答，断开重连")
                await self._ws.close(4000)
                return

            self._heartbeat_acked = False
            await self._send_heartbeat()
            await asyncio.sleep(self._heartbeat_interval)

    async def _config_reload_loop(self) -> None:
        """配置热更新检测循环

        定期检查 config.yml 是否有变更，
        有变更时重载配置并刷新 Presence。
        """
        interval = self.config.config_reload_interval
        log.debug("配置热更新循环启动，检查间隔: %ds", interval)

        while True:
            await asyncio.sleep(interval)
            try:
                if self.config.has_changed():
                    self.config.reload()
                    log.info("配置已更新，刷新 Presence")
                    await self._update_presence()
                    # 更新检查间隔（可能已变更）
                    interval = self.config.config_reload_interval
            except Exception as e:
                log.warning("配置热更新检查失败: %s", e)

    # ═════════════════════════════════════════════════════
    # 控制方法
    # ═════════════════════════════════════════════════════

    def stop(self) -> None:
        """优雅停止客户端

        设置运行标记为 False，保存运行时间，主循环将在当前迭代结束后退出。
        """
        log.info("收到停止信号，准备优雅关闭...")
        self._running = False
        self._save_elapsed_time()
        log.info("客户端已停止")

    def _save_elapsed_time(self) -> None:
        """保存 auto 模式下的已运行时间到 config.yml

        仅在 start_time_mode 为 auto 时执行，
        将当前已运行分钟数写入 auto_save_minutes 字段。
        """
        if self.config.start_time_mode == "auto":
            elapsed_minutes = (time.time() - self.start_ts) / 60
            log.debug("保存 auto 模式运行时间: %.1f 分钟", elapsed_minutes)
            try:
                self.config.save_auto_minutes(elapsed_minutes)
            except Exception as e:
                log.warning("保存运行时间失败: %s", e)
