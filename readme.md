# Grok 账号批量注册工具

基于 [DrissionPage](https://github.com/g1879/DrissionPage) 的 Grok (x.ai) 账号自动注册脚本，使用自建 Cloudflare 临时邮箱实例接收验证码，通过 Chrome 扩展修复 CDP `MouseEvent.screenX/screenY` 缺陷绕过 Cloudflare Turnstile。

注册完成后自动推送 SSO token 到 [grok2api](https://github.com/chenyme/grok2api) 号池。

> 重要：这个仓库**不包含临时邮箱后端**。使用脚本前，必须先自行部署一个兼容的临时邮箱服务，并拿到它的 API 地址与邮箱域名。可直接参考并部署 [dreamhunter2333/cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email)。

## 先决条件（先做这个）

1. 先部署兼容的临时邮箱服务（推荐直接部署 [dreamhunter2333/cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email)）
2. 确认它实现了脚本依赖的 3 个接口：`POST /api/new_address`、`GET /api/parsed_mails`、`GET /api/parsed_mail/:id`
3. 确认你能实际收到 x.ai 验证邮件
4. 把临时邮箱服务地址填到 `temp_email_api_base`，把邮箱域名填到 `temp_email_domain`

如果还没部署临时邮箱，请先完成这一步，再继续安装和运行本仓库。

## 特性

- 自定义临时邮箱 API（默认示例为 `https://mail.example.com`）
- 自定义邮箱域名（默认示例为 `example.com`）
- Cloudflare Turnstile 自动绕过（Chrome 扩展 patch `MouseEvent.screenX/screenY`）
- 无头服务器支持（Xvfb 虚拟显示器，自动检测 Linux 环境）
- 中英文界面自动适配
- 自动推送 SSO token 到 grok2api
- 批量运行延迟抖动与可调验证码轮询超时

---

## 环境要求

- Python 3.10+
- Chromium 或 Chrome 浏览器
- 可用临时邮箱实例，且提供：
  - `POST /api/new_address`
  - `GET /api/parsed_mails`
  - `GET /api/parsed_mail/:id`
- 可选：[grok2api](https://github.com/chenyme/grok2api) 实例（用于自动导入 SSO token）

---

## 安装

完成上面“先决条件”后，再安装本项目依赖：

```bash
pip install -r requirements.txt
```

无头服务器（Linux）额外安装：

```bash
apt install -y xvfb
pip install PyVirtualDisplay
# 推荐用 playwright 装 chromium（避免 snap 版 AppArmor 限制）
pip install playwright && python -m playwright install chromium && python -m playwright install-deps chromium
```

---

## 配置文件（config.json）

部署好临时邮箱服务后，再填写这里的 `temp_email_api_base` 和 `temp_email_domain`。

```bash
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
    "run": {
        "count": 10,
        "delay_min_seconds": 2,
        "delay_max_seconds": 4
    },
    "temp_email_api_base": "https://mail.example.com",
    "temp_email_domain": "example.com",
    "verification_timeout_seconds": 120,
    "mail_poll_interval_seconds": 3,
    "verify_tls": true,
    "proxy": "",
    "browser_proxy": "",
    "api": {
        "endpoint": "http://localhost:21434",
        "token": "grok2api",
        "append": true,
        "pool": "basic",
        "verify_tls": true
    }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `run.count` | int | 注册轮数，`0` 为无限循环，可通过 `--count` 覆盖 |
| `run.delay_min_seconds` | number | 轮次间最小等待秒数 |
| `run.delay_max_seconds` | number | 轮次间最大等待秒数 |
| `temp_email_api_base` | string | 临时邮箱 API 根地址 |
| `temp_email_domain` | string | 创建邮箱时使用域名 |
| `verification_timeout_seconds` | int | 等待验证码总超时 |
| `mail_poll_interval_seconds` | number | 轮询收件箱间隔 |
| `verify_tls` | bool | 临时邮箱 HTTPS 是否校验证书，默认 `true` |
| `proxy` | string | 临时邮箱 API 请求代理（可选） |
| `browser_proxy` | string | 浏览器代理，无头服务器需翻墙时填写（可选） |
| `api.endpoint` | string | grok2api 管理接口根地址，或 `/admin/api`、`/admin/api/tokens`、`/admin/api/tokens/add` 之一；留空跳过推送 |
| `api.token` | string | grok2api 的 `app_key` |
| `api.append` | bool | `true` 走追加接口，`false` 走整池覆盖接口 |
| `api.pool` | string | grok2api 目标池名，默认 `basic` |
| `api.verify_tls` | bool | grok2api HTTPS 是否校验证书，默认 `true` |

---

## 临时邮箱接口要求

当前脚本假设你的临时邮箱服务接口行为如下：

### 1. 创建邮箱

```http
POST /api/new_address
Content-Type: application/json

{
  "name": "tmpabc123",
  "domain": "example.com"
}
```

预期返回：

```json
{
  "address": "tmpabc123@example.com",
  "jwt": "<mailbox_jwt>",
  "password": "optional"
}
```

### 2. 查询邮件列表

```http
GET /api/parsed_mails?limit=10&offset=0
Authorization: Bearer <mailbox_jwt>
```

### 3. 查询单封邮件详情

```http
GET /api/parsed_mail/:id
Authorization: Bearer <mailbox_jwt>
```

脚本会从 `text` / `html` 中提取 `XXX-XXX` 或 6 位数字验证码。

---

## 启动方式

```bash
# 按 config.json 中 run.count 执行（默认 10 轮）
python DrissionPage_example.py

# 指定轮数
python DrissionPage_example.py --count 50

# 无限循环
python DrissionPage_example.py --count 0
```

无头服务器会自动启用 Xvfb，无需额外配置。

---

## 输出文件

```
sso/
  sso_<timestamp>.txt     ← 每行一个 SSO token
logs/
  run_<timestamp>.log     ← 每轮注册的邮箱、密码和结果
```

目录在首次运行时自动创建。

---

## 文件结构

```
├── DrissionPage_example.py     # 主脚本
├── email_register.py           # 临时邮箱接口封装
├── config.json                 # 配置文件（不入库）
├── config.example.json         # 配置模板
├── requirements.txt            # Python 依赖
├── turnstilePatch/             # Chrome 扩展（Turnstile patch）
│   ├── manifest.json
│   └── script.js
├── sso/                        # SSO token 输出（自动创建）
└── logs/                       # 运行日志（自动创建）
```

---

## 无头服务器部署注意

- snap 版 chromium 在 root 下有 AppArmor 限制，推荐用 playwright 安装的 chromium
- 服务器直连 x.ai 可能被墙，需在 `browser_proxy` 填写代理地址
- 脚本自动检测 Linux 环境并启用 Xvfb + playwright chromium 路径

---

## 致谢

- [ReinerBRO/grok-register](https://github.com/ReinerBRO/grok-register) — 原始项目
- [grok2api](https://github.com/chenyme/grok2api) — Grok API 代理
- [dreamhunter2333/cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) — 临时邮箱后端部署项目
