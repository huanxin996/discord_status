# HX Discord Status

通过 Discord Gateway WebSocket 设置自定义 Rich Presence 状态的命令行工具。无需运行 Discord 客户端，可在服务器上持续运行。

---

## 功能

- 自定义游戏名称、详情文字、状态文字
- 自定义大图标 / 小图标及悬浮提示
- 自定义已运行时间（自动计时 / 固定时长 / 不显示）
- 最多 2 个可点击按钮
- 5 种活动类型（游玩 / 直播 / 听歌 / 观看 / 竞技）
- 热更新：修改 `config.yml` 后自动重载，无需重启
- 自动重连：断线后指数退避重连，可配置最大重连次数
- 会话恢复：支持 Gateway Resume
- auto 模式时间持久化：断线或退出时自动保存已运行时长，重连后恢复计时
- QR 扫码登录 / 本地 Token 提取（Windows DPAPI）/ 手动输入
- YAML 配置文件，支持注释

---

## 项目结构

```text
hx_discord_status/
├── main.py          # 入口：加载配置、初始化日志、启动客户端
├── discord.py       # Gateway WebSocket 客户端（连接、心跳、Presence、重连）
├── config.py        # YAML 配置管理（读写、热更新检测、字段校验）
├── login.py         # Token 获取（QR 扫码、DPAPI 本地提取、手动输入）
├── logger.py        # 日志系统（RotatingFileHandler，控制台/文件分级输出）
├── dsclass.py       # 常量与枚举（OpCode、活动类型、状态类型、网关参数）
├── config.yml       # 配置文件
├── requirements.txt # Python 依赖
└── log/             # 日志输出目录（运行时自动创建）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：`websockets`、`pyyaml`、`cryptography`（登录模块）、`qrcode`（QR 登录）。

### 2. 获取 Discord Token

```bash
python main.py --login
```

提供三种方式：

1. **QR 扫码登录**（跨平台）— 终端显示二维码，手机 Discord 扫码确认
2. **本地提取**（仅 Windows）— 从本地 Discord 存储通过 DPAPI 解密读取
3. **手动输入** — 直接粘贴 Token

获取成功后自动写入 `config.yml`。

### 3. 创建 Discord Application（可选）

如果需要显示图标、详情文字或按钮：

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 点击 **New Application**
3. 复制 **Application ID** 填入 `config.yml` 的 `application_id`
4. 左侧 **Rich Presence → Art Assets** 上传图标，记住 asset 名称

> 不需要图标和详情时可跳过此步，只填 `token` 和 `game_name` 即可。

### 4. 编辑配置

编辑 `config.yml`，文件内有详细注释说明每个字段：

```yaml
token: "你的Token"
game_name: "VS Code"
activity_type: 0          # 0=游玩 1=直播 2=听 3=看 5=竞技
details: "正在写代码"      # 需要 application_id
state: "项目：My Project"  # 需要 application_id

application_id: "你的AppID"
large_image_key: "vscode"
large_image_text: "VS Code"
small_image_key: "python"
small_image_text: "Python 3"

buttons:
  - label: "查看项目"
    url: "https://github.com"

start_time_mode: "auto"    # auto / custom / none
custom_elapsed_minutes: 0
auto_save_minutes: 0       # auto 模式自动维护，无需手动修改

status: "online"           # online / idle / dnd / invisible
reconnect_delay: 5
max_reconnect_attempts: 0  # 0 = 无限重连
config_reload_interval: 60

logging:
  console_level: "INFO"
  file_level: "DEBUG"
  max_file_size_mb: 5
  backup_count: 5
```

### 5. 启动

```bash
python main.py
```

---

## 内置模板

`config.yml` 末尾包含 5 套注释掉的模板示例，取消注释并替换对应字段即可使用：

| 模板 | 场景 |
| ---- | ---- |
| 模板 1 | 极简 — 只显示游戏名，无需 Application |
| 模板 2 | 程序员 — VS Code 编码状态 |
| 模板 3 | 听歌 — 网易云音乐 activity_type=2 |
| 模板 4 | 直播 — Minecraft 直播状态 |
| 模板 5 | 学习 — 专注模式，自定义已用时长 |

---

## 服务器部署

**Linux systemd：**

```bash
# 上传项目到服务器
scp -r hx_discord_status/ user@server:/opt/hx_discord_status/

# 安装依赖
pip3 install -r /opt/hx_discord_status/requirements.txt

# 获取 Token（在本地运行后复制 config.yml，或在服务器上手动填入）
# 启动
cd /opt/hx_discord_status && python3 main.py
