"""
HX Discord Status — 登录模块 (login.py)

提供三种 Discord Token 获取方式：
  1. QR 码扫码登录（跨平台，推荐）
  2. 本地自动提取（仅 Windows，DPAPI + AES-GCM 解密）
  3. 手动输入

可作为独立脚本运行：python login.py
也可被 main.py 导入使用其中的函数。

依赖: websockets, cryptography, qrcode(可选), dsclass, config, logger
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# ── 第三方依赖（带友好错误提示） ────────────────────────
try:
    import websockets
except ImportError:
    print("[错误] 缺少 websockets 库：pip install websockets")
    sys.exit(1)

try:
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes, serialization
except ImportError:
    print("[错误] 缺少 cryptography 库：pip install cryptography")
    sys.exit(1)

try:
    import qrcode
except ImportError:
    qrcode = None

# ── 项目内部依赖 ──────────────────────────────────────────
from dsclass import REMOTE_AUTH_URL, DISCORD_API, DISCORD_UA
from config import AppConfig, CONFIG_PATH
from logger import setup_logger, get_logger

log = get_logger("login")


# ═══════════════════════════════════════════════════════════
# QR 码终端显示
# ═══════════════════════════════════════════════════════════

def print_qr_terminal(url: str) -> None:
    """在终端以 Unicode 半块字符打印 QR 码

    Args:
        url: 要编码的 URL
    """
    log.debug("生成 QR 码: %s", url)
    if qrcode:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Unicode 半块字符：上半 ▀、下半 ▄、全满 █、空白
        matrix = qr.get_matrix()
        rows = len(matrix)
        lines = []
        for r in range(0, rows, 2):
            line = ""
            for c in range(len(matrix[r])):
                top = matrix[r][c]
                bot = matrix[r + 1][c] if r + 1 < rows else False
                if top and bot:
                    line += "█"
                elif top and not bot:
                    line += "▀"
                elif not top and bot:
                    line += "▄"
                else:
                    line += " "
            lines.append(line)
        print("\n".join(lines))
    else:
        log.warning("未安装 qrcode 库，无法在终端显示二维码")
        print("  (未安装 qrcode 库，无法显示二维码)")
        print("  请将以下链接粘贴到任意 QR 码生成器中：")

    print()
    print(f"  链接: {url}")


# ═══════════════════════════════════════════════════════════
# 方式一：QR 码扫码登录（Remote Auth Gateway）
# ═══════════════════════════════════════════════════════════

async def qr_login() -> str | None:
    """通过 Discord Remote Auth Gateway 实现 QR 码扫码登录

    流程:
        1. 生成 RSA-2048 密钥对
        2. 连接 Remote Auth Gateway
        3. 发送公钥 → 接收 nonce 挑战 → 响应 proof
        4. 获取 fingerprint → 生成 QR 码
        5. 等待用户手机扫码确认
        6. 用 ticket 换取加密 token
        7. RSA-OAEP 解密得到明文 token

    Returns:
        Token 字符串，失败返回 None
    """
    log.info("生成 RSA-2048 密钥对 ...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()

    # 导出公钥为 SPKI DER → base64 编码
    pub_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    encoded_public_key = base64.b64encode(pub_der).decode()
    log.debug("公钥已编码, 长度: %d 字符", len(encoded_public_key))

    log.info("连接 Discord Remote Auth Gateway ...")
    log.debug("Gateway 地址: %s", REMOTE_AUTH_URL)

    try:
        async with websockets.connect(
            REMOTE_AUTH_URL,
            max_size=2 ** 20,
            close_timeout=10,
            additional_headers={"Origin": "https://discord.com"},
        ) as ws:
            # ── 1. 接收 Hello ─────────────────────────
            msg = await _recv_skip_ack(ws, expect_op="hello", timeout=10)
            if msg is None:
                log.error("未收到 Hello 消息，连接失败")
                return None
            heartbeat_interval = msg.get("heartbeat_interval", 41250) / 1000
            log.debug("Remote Auth 心跳间隔: %.2fs", heartbeat_interval)

            # ── 启动心跳协程 ──────────────────────────
            hb_task = asyncio.create_task(_heartbeat(ws, heartbeat_interval))

            try:
                # ── 2. 发送 init ──────────────────────
                log.debug("发送 init 消息，附带公钥")
                await ws.send(json.dumps({
                    "op": "init",
                    "encoded_public_key": encoded_public_key,
                }))

                # ── 3. nonce_proof 挑战 ───────────────
                msg = await _recv_skip_ack(ws, expect_op="nonce_proof", timeout=10)
                if msg is None:
                    log.error("未收到 nonce_proof 挑战")
                    return None

                log.debug("解密 nonce 并生成 proof...")
                encrypted_nonce = base64.b64decode(msg["encrypted_nonce"])
                decrypted_nonce = private_key.decrypt(
                    encrypted_nonce,
                    padding.OAEP(
                        mgf=padding.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
                nonce_hash = hashlib.sha256(decrypted_nonce).digest()
                proof = base64.urlsafe_b64encode(nonce_hash).rstrip(b"=").decode()

                log.debug("发送 nonce_proof 响应")
                await ws.send(json.dumps({
                    "op": "nonce_proof",
                    "proof": proof,
                }))

                # ── 4. pending_remote_init → QR 码 ────
                msg = await _recv_skip_ack(ws, expect_op="pending_remote_init", timeout=10)
                if msg is None:
                    log.error("未收到 pending_remote_init 响应")
                    return None

                fingerprint = msg["fingerprint"]
                log.debug("收到 fingerprint: %s", fingerprint)
                qr_url = f"https://discord.com/ra/{fingerprint}"

                print()
                print("=" * 52)
                print("  请使用手机 Discord 扫描下方二维码")
                print("  手机端路径: 设置 → 扫一扫 二维码")
                print("=" * 52)
                print()
                print_qr_terminal(qr_url)
                print()
                log.info("等待扫码确认（2 分钟超时）...")

                # ── 5. 等待扫码 → ticket ──────────────
                ticket = await _wait_for_ticket(ws, private_key, timeout=120)
                if not ticket:
                    log.warning("扫码超时或被取消")
                    return None

                # ── 6. ticket → encrypted_token ───────
                log.info("正在换取 Token ...")
                log.debug("使用 ticket 换取加密 Token, ticket 长度: %d", len(ticket))
                token = await _exchange_ticket(ticket, private_key)
                if token:
                    log.debug("Token 解密成功, 长度: %d", len(token))
                return token

            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        log.error("远程登录流程异常: %s", e)
        return None


async def _recv_skip_ack(ws, expect_op: str, timeout: float) -> dict | None:
    """接收消息并跳过 heartbeat_ack，直到收到期望的 op

    Args:
        ws: WebSocket 连接
        expect_op: 期望的操作码字符串
        timeout: 超时秒数

    Returns:
        消息字典，超时或操作码不匹配返回 None
    """
    deadline = time.time() + timeout
    log.debug("等待消息: 期望 op=%s, 超时=%.1fs", expect_op, timeout)
    while time.time() < deadline:
        remaining = deadline - time.time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.5))
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        if msg.get("op") == "heartbeat_ack":
            log.debug("跳过 heartbeat_ack")
            continue
        if msg.get("op") == expect_op:
            log.debug("收到期望的 op=%s", expect_op)
            return msg
        log.warning("期望 %s，收到: %s", expect_op, msg.get("op"))
        return None
    log.error("等待 %s 超时", expect_op)
    return None


async def _heartbeat(ws, interval: float) -> None:
    """Remote Auth Gateway 心跳协程"""
    log.debug("Remote Auth 心跳协程启动, 间隔: %.2fs", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(json.dumps({"op": "heartbeat"}))
            log.debug("Remote Auth 心跳包已发送")
        except Exception as e:
            log.debug("Remote Auth 心跳发送失败: %s", e)
            break


async def _wait_for_ticket(ws, private_key, timeout: float) -> str | None:
    """等待用户扫码确认并返回 ticket

    Args:
        ws: WebSocket 连接
        private_key: RSA 私钥（用于解密用户信息）
        timeout: 超时秒数

    Returns:
        ticket 字符串，超时/取消返回 None
    """
    deadline = time.time() + timeout
    log.debug("等待扫码确认, 超时: %.0fs", timeout)

    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            log.debug("等待扫码中... (剩余 %.0fs)", deadline - time.time())
            continue
        except Exception as e:
            log.warning("等待扫码时连接异常: %s", e)
            break

        msg = json.loads(raw)
        op = msg.get("op")

        if op == "heartbeat_ack":
            log.debug("跳过 heartbeat_ack")
            continue

        if op == "pending_ticket":
            # 解密用户信息并显示
            try:
                encrypted = base64.b64decode(msg["encrypted_user_payload"])
                decrypted = private_key.decrypt(
                    encrypted,
                    padding.OAEP(
                        mgf=padding.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
                # 格式: "user_id:discriminator:avatar_hash:username"
                parts = decrypted.decode("utf-8").split(":")
                if len(parts) >= 4:
                    uid, discrim = parts[0], parts[1]
                    uname = ":".join(parts[3:])
                    name = uname if discrim == "0" else f"{uname}#{discrim}"
                    log.info("检测到账号: %s (ID: %s)", name, uid)
                    log.info("请在手机上点击「确认登录」...")
            except Exception as e:
                log.debug("解密用户信息失败: %s", e)
                log.info("已检测到扫码，等待确认 ...")
            continue

        if op == "pending_login":
            log.info("用户已确认登录！")
            log.debug("ticket 长度: %d", len(msg.get("ticket", "")))
            return msg["ticket"]

        if op == "cancel":
            log.warning("用户在手机上取消了登录")
            return None

    return None


async def _exchange_ticket(ticket: str, private_key) -> str | None:
    """用 ticket 换取加密 Token 并解密

    Args:
        ticket: 登录 ticket
        private_key: RSA 私钥

    Returns:
        明文 Token 字符串，失败返回 None
    """
    url = f"{DISCORD_API}/users/@me/remote-auth/login"
    log.debug("发送 Token 换取请求: POST %s", url)
    req = urllib.request.Request(
        url,
        data=json.dumps({"ticket": ticket}).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.debug("Token 换取响应状态码: %d", resp.status)
            result = json.loads(resp.read())
    except Exception as e:
        log.error("Token 换取失败: %s", e)
        return None

    encrypted_token_b64 = result.get("encrypted_token")
    if not encrypted_token_b64:
        log.error("响应中缺少 encrypted_token, 响应键: %s", list(result.keys()))
        return None

    # RSA-OAEP SHA-256 解密
    log.debug("开始 RSA-OAEP 解密 Token...")
    encrypted_token = base64.b64decode(encrypted_token_b64)
    token = private_key.decrypt(
        encrypted_token,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    ).decode("utf-8")

    return token


# ═══════════════════════════════════════════════════════════
# 方式二：本地自动提取（Windows DPAPI + AES-256-GCM）
# ═══════════════════════════════════════════════════════════

def try_local_extract() -> str | None:
    """从本地 Discord 客户端存储中解密提取 Token

    仅支持 Windows 平台。
    解密链: LevelDB → dQw4w9WgXcQ 前缀 → DPAPI 解密 master key → AES-GCM 解密 token

    Returns:
        Token 字符串，失败/不支持返回 None
    """
    if sys.platform != "win32":
        log.warning("本地提取仅支持 Windows 平台, 当前: %s", sys.platform)
        return None

    log.debug("开始本地 Token 提取流程...")
    import ctypes
    import ctypes.wintypes

    # ── DPAPI 数据结构 ────────────────────────────────
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def dpapi_decrypt(encrypted: bytes) -> bytes:
        """使用 Windows DPAPI 解密数据"""
        p_in = DATA_BLOB(
            len(encrypted),
            ctypes.cast(
                ctypes.create_string_buffer(encrypted, len(encrypted)),
                ctypes.POINTER(ctypes.c_char),
            ),
        )
        p_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(p_in), None, None, None, None, 0, ctypes.byref(p_out)
        ):
            return b""
        result = ctypes.string_at(p_out.pbData, p_out.cbData)
        ctypes.windll.kernel32.LocalFree(p_out.pbData)
        return result

    # ── 遍历 Discord 客户端目录 ───────────────────────
    appdata = os.environ.get("APPDATA", "")
    log.debug("APPDATA 路径: %s", appdata)
    for variant in ("discord", "discordcanary", "discordptb"):
        app_dir = Path(appdata) / variant
        local_state = app_dir / "Local State"
        leveldb = app_dir / "Local Storage" / "leveldb"

        if not local_state.exists() or not leveldb.exists():
            log.debug("跳过 %s: 目录不存在", variant)
            continue

        log.debug("扫描 %s 客户端目录: %s", variant, app_dir)

        # 读取并解密 master key
        try:
            with open(local_state, "r", encoding="utf-8") as f:
                state = json.load(f)
            b64_key = state["os_crypt"]["encrypted_key"]
            encrypted_key = base64.b64decode(b64_key)[5:]  # 跳过 "DPAPI" 前缀
            master_key = dpapi_decrypt(encrypted_key)
            if not master_key:
                log.warning("%s: DPAPI 解密 master key 失败", variant)
                continue
            log.debug("%s: master key 解密成功, 长度: %d", variant, len(master_key))
        except Exception as e:
            log.debug("%s: 读取 Local State 失败: %s", variant, e)
            continue

        # 在 LevelDB 中搜索加密 Token
        log.debug("%s: 扫描 LevelDB 目录: %s", variant, leveldb)
        token_pattern = re.compile(r"dQw4w9WgXcQ:[A-Za-z0-9+/=]+")
        for db_file in sorted(
            leveldb.iterdir(),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            if db_file.suffix not in (".ldb", ".log"):
                continue
            try:
                data = db_file.read_bytes().decode("utf-8", errors="ignore")
                for enc_str in token_pattern.findall(data):
                    try:
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

                        payload = base64.b64decode(enc_str.split(":", 1)[1])
                        nonce = payload[3:15]       # 12 字节 nonce
                        ciphertext = payload[15:]   # AES-GCM 密文 + tag
                        token = AESGCM(master_key).decrypt(
                            nonce, ciphertext, None
                        ).decode("utf-8")

                        # 验证 Token 格式
                        if re.match(
                            r"[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{5,8}\.[A-Za-z0-9_-]{25,50}",
                            token,
                        ):
                            log.info("从 %s 成功提取 Token", variant)
                            log.debug("Token 来源文件: %s", db_file.name)
                            return token
                    except Exception as e:
                        log.debug("解密候选 Token 失败: %s", e)
                        continue
            except Exception as e:
                log.debug("读取 LevelDB 文件失败 (%s): %s", db_file.name, e)
                continue

    log.debug("未在任何 Discord 客户端中找到有效 Token")
    return None


# ═══════════════════════════════════════════════════════════
# Token 验证
# ═══════════════════════════════════════════════════════════

def verify_token(token: str) -> dict | None:
    """通过 Discord REST API 验证 Token 是否有效

    Args:
        token: Discord 用户 Token

    Returns:
        用户信息字典 (id, username, discriminator, ...)，无效返回 None
    """
    log.debug("验证 Token: %s...", token[:8] if len(token) > 8 else "***")
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Authorization": token,
                "User-Agent": DISCORD_UA,
            },
        )
        log.debug("发送 Token 验证请求: GET /users/@me")
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                user_data = json.loads(resp.read())
                log.debug("Token 验证成功, 用户ID: %s", user_data.get("id", "?"))
                return user_data
            log.warning("Token 验证返回非 200 状态码: %d", resp.status)
    except Exception as e:
        log.warning("Token 验证请求失败: %s", e)
    return None


# ═══════════════════════════════════════════════════════════
# 交互式登录入口
# ═══════════════════════════════════════════════════════════

def run_login() -> None:
    """交互式登录流程

    提供菜单让用户选择获取 Token 的方式，
    验证后自动写入 config.yml。
    """
    print()
    print("=" * 52)
    print("  HX Discord Status — 登录工具")
    print("=" * 52)
    print()
    print("  [1] QR 码扫码登录（推荐，需要手机 Discord）")
    print("  [2] 从本地 Discord 提取 Token（仅 Windows）")
    print("  [3] 手动输入 Token")
    print()

    choice = input("  请选择登录方式 (1/2/3): ").strip()
    log.debug("用户选择登录方式: %s", choice)
    token = None

    if choice == "1":
        print()
        token = asyncio.run(qr_login())

    elif choice == "2":
        print()
        log.info("正在从本地 Discord 存储中提取 ...")
        token = try_local_extract()
        if not token:
            log.warning("未找到有效 Token，请尝试其他方式")

    elif choice == "3":
        print()
        token = input("  请粘贴你的 Discord Token: ").strip()
        if not token:
            print("[取消] 未输入 Token")
            return

    else:
        log.warning("无效选择 '%s'，默认使用 QR 码登录", choice)
        print("[提示] 无效选择，默认使用 QR 码登录")
        print()
        token = asyncio.run(qr_login())

    # ── 验证 & 保存 ──────────────────────────────────
    if not token:
        log.error("未能获取 Token")
        sys.exit(1)

    print()
    print(f"[Token] {token[:20]}...（已隐藏后半部分）")
    print()
    log.info("正在验证 Token ...")

    user_info = verify_token(token)
    if user_info:
        uname = user_info.get("username", "?")
        discrim = user_info.get("discriminator", "0")
        uid = user_info.get("id", "?")
        name = uname if discrim == "0" else f"{uname}#{discrim}"
        log.info("登录成功: %s  (ID: %s)", name, uid)
    else:
        log.warning("Token 未通过 API 验证（可能是网络问题）")
        confirm = input("  是否仍然保存？(y/N): ").strip().lower()
        if confirm != "y":
            print("[取消] 未保存")
            return

    # 写入 config.yml
    cfg = AppConfig(strict=False)
    cfg.update_token(token)

    print()
    log.info("Token 已保存到 config.yml")
    print()
    print("  现在可以运行: python main.py")
    print()


# ═══════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 作为独立脚本运行时，初始化日志（使用默认设置）
    setup_logger(console_level="INFO", file_level="DEBUG")
    run_login()
