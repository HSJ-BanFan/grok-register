
from __future__ import annotations

import datetime
import email
import imaplib
import json
import os
import random
import re
import string
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# Cloudflare Temp Email 配置（从 config.json 加载）
# ============================================================

_config_path = Path(__file__).parent / "config.json"
_conf: Dict[str, Any] = {}
if _config_path.exists():
    with _config_path.open("r", encoding="utf-8") as _f:
        _conf = json.load(_f)

# 你的 Cloudflare Temp Email 实例地址和域名
TEMP_EMAIL_API_BASE = str(_conf.get("temp_email_api_base", "https://mail.example.com"))
TEMP_EMAIL_DOMAIN = str(_conf.get("temp_email_domain", "example.com"))
VERIFICATION_TIMEOUT_SECONDS = int(_conf.get("verification_timeout_seconds", 120))
MAIL_POLL_INTERVAL_SECONDS = float(_conf.get("mail_poll_interval_seconds", 3))
VERIFY_TLS = bool(_conf.get("verify_tls", True))
PROXY = str(_conf.get("proxy", ""))
IMAP_TOKEN_SENTINEL = "imap"
IMAP_CONFIG = _conf.get("imap", {}) if isinstance(_conf.get("imap", {}), dict) else {}
MIN_IMAP_POLL_INTERVAL_SECONDS = 5.0
MAX_IMAP_MESSAGES_TO_CHECK = 10

# ============================================================
# 适配层：为 DrissionPage_example.py 提供简单接口
# ============================================================

_temp_email_cache: Dict[str, str] = {}


def get_imap_password() -> str:
    password_env = str(IMAP_CONFIG.get("password_env", "")).strip()
    if password_env:
        return os.environ.get(password_env, "").strip()
    return str(IMAP_CONFIG.get("password", "")).strip()


def is_imap_enabled() -> bool:
    return bool(IMAP_CONFIG.get("enabled") and IMAP_CONFIG.get("user") and get_imap_password())


def should_use_imap(mail_token: str, email_address: str) -> bool:
    configured_user = str(IMAP_CONFIG.get("user", "")).strip().lower()
    normalized_email = str(email_address or "").strip().lower()
    return is_imap_enabled() and (mail_token == IMAP_TOKEN_SENTINEL or normalized_email == configured_user)


def get_email_and_token() -> Tuple[Optional[str], Optional[str]]:
    """
    创建临时邮箱并返回 (email, jwt_token)。
    供 DrissionPage_example.py 调用。
    """
    if is_imap_enabled():
        return str(IMAP_CONFIG.get("user", "")).strip(), IMAP_TOKEN_SENTINEL

    email_address, _password, jwt_token = create_temp_email()
    if email_address and jwt_token:
        _temp_email_cache[email_address] = jwt_token
        return email_address, jwt_token
    return None, None


def get_oai_code(dev_token: str, email: str, timeout: int = VERIFICATION_TIMEOUT_SECONDS) -> Optional[str]:
    """
    轮询临时邮箱获取 OTP 验证码。
    供 DrissionPage_example.py 调用。

    Returns:
        验证码字符串（去除连字符，如 "MM0SF3"）或 None
    """
    if should_use_imap(dev_token, email):
        code = wait_for_code_via_imap(timeout=timeout)
    else:
        code = wait_for_verification_code(mail_token=dev_token, timeout=timeout)
    if code:
        code = code.replace("-", "")
    return code


# ============================================================
# HTTP 会话
# ============================================================

def _create_session() -> requests.Session:
    """创建请求会话"""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s


# ============================================================
# Cloudflare Temp Email 核心函数
# ============================================================

def create_temp_email() -> Tuple[str, str, str]:
    """创建临时邮箱，返回 (email, password, jwt_token)"""
    chars = string.ascii_lowercase + string.digits
    length = random.randint(8, 13)
    name = "".join(random.choice(chars) for _ in range(length))

    api_base = TEMP_EMAIL_API_BASE.rstrip("/")
    session = _create_session()

    try:
        res = session.post(
            f"{api_base}/api/new_address",
            json={"name": name, "domain": TEMP_EMAIL_DOMAIN},
            timeout=15,
            verify=VERIFY_TLS,
        )
        if res.status_code != 200:
            raise Exception(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")

        data = res.json()
        address = data.get("address", "")
        jwt_token = data.get("jwt", "")
        password = data.get("password", "")

        if not address or not jwt_token:
            raise Exception(f"创建邮箱返回数据不完整: {data}")

        print(f"[*] 临时邮箱创建成功: {address}")
        return address, password or "", jwt_token

    except Exception as e:
        raise Exception(f"创建临时邮箱失败: {e}")


def fetch_emails(mail_token: str) -> List[Dict[str, Any]]:
    """获取邮件列表（已解析格式）"""
    try:
        api_base = TEMP_EMAIL_API_BASE.rstrip("/")
        headers = {"Authorization": f"Bearer {mail_token}"}
        session = _create_session()
        res = session.get(
            f"{api_base}/api/parsed_mails?limit=10&offset=0",
            headers=headers,
            timeout=15,
            verify=VERIFY_TLS,
        )
        if res.status_code == 200:
            data = res.json()
            return data.get("results", [])
    except Exception as e:
        print(f"[Debug] 获取邮件列表失败: {e}")
    return []


def fetch_email_detail(mail_token: str, msg_id: str) -> Optional[Dict]:
    """获取单封邮件详情（已解析格式，包含 text/html）"""
    try:
        api_base = TEMP_EMAIL_API_BASE.rstrip("/")
        headers = {"Authorization": f"Bearer {mail_token}"}
        session = _create_session()
        res = session.get(
            f"{api_base}/api/parsed_mail/{msg_id}",
            headers=headers,
            timeout=15,
            verify=VERIFY_TLS,
        )
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[Debug] 获取邮件详情失败: {e}")
    return None


def extract_imap_message_content(raw_message: bytes) -> str:
    message = email.message_from_bytes(raw_message)
    parts: List[str] = []

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
        else:
            parts.append(str(message.get_payload() or ""))

    return "\n".join(parts)


def build_imap_search_filter() -> str:
    search_filter = str(IMAP_CONFIG.get("search_filter", "")).strip()
    if search_filter:
        return search_filter

    since = datetime.datetime.now().strftime("%d-%b-%Y")
    return f'(SINCE "{since}" FROM "info@x.ai")'


def get_imap_port() -> int:
    try:
        port = int(IMAP_CONFIG.get("port", 993))
    except (TypeError, ValueError) as exc:
        raise Exception("IMAP 配置错误：port 必须是 1-65535 的整数") from exc

    if not 1 <= port <= 65535:
        raise Exception("IMAP 配置错误：port 必须是 1-65535 的整数")
    return port


def wait_for_code_via_imap(
    timeout: int = VERIFICATION_TIMEOUT_SECONDS,
    client_factory=imaplib.IMAP4_SSL,
) -> Optional[str]:
    server = str(IMAP_CONFIG.get("server", "imap.gmail.com")).strip() or "imap.gmail.com"
    port = get_imap_port()
    user = str(IMAP_CONFIG.get("user", "")).strip()
    password = get_imap_password()
    poll_interval = max(MIN_IMAP_POLL_INTERVAL_SECONDS, MAIL_POLL_INTERVAL_SECONDS)
    search_filter = build_imap_search_filter()
    seen_message_ids = set()
    start = time.time()

    if not user or not password:
        raise Exception("IMAP 配置不完整：缺少 user 或 password")

    print(f"[*] 开始通过 IMAP 轮询 Gmail，等待验证码，超时时间: {timeout} 秒")
    while time.time() - start < timeout:
        client = None
        try:
            client = client_factory(server, port)
            client.login(user, password)
            client.select("INBOX")
            search_status, search_data = client.search(None, search_filter)
            if search_status == "OK" and search_data:
                message_ids = search_data[0].split()
                for message_id in reversed(message_ids[-MAX_IMAP_MESSAGES_TO_CHECK:]):
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)

                    fetch_status, fetched = client.fetch(message_id, "(RFC822)")
                    if fetch_status != "OK" or not fetched:
                        continue
                    for item in fetched:
                        if not isinstance(item, tuple) or len(item) < 2:
                            continue
                        content = extract_imap_message_content(item[1])
                        code = extract_verification_code(content)
                        if code:
                            print(f"[*] 从 Gmail IMAP 提取到验证码: {code}")
                            return code
        except imaplib.IMAP4.error as e:
            if "AUTHENTICATIONFAILED" in str(e).upper():
                raise Exception("IMAP 登录失败，请检查 Gmail 账号、应用专用密码和 IMAP 是否启用") from e
            print(f"[Debug] IMAP 获取验证码失败: {type(e).__name__}")
        except OSError as e:
            print(f"[Debug] IMAP 网络连接失败: {type(e).__name__}")
        finally:
            if client is not None:
                try:
                    client.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

        time.sleep(poll_interval)

    print("[*] IMAP 轮询超时，未收到或未解析出验证码")
    return None


def wait_for_verification_code(mail_token: str, timeout: int = VERIFICATION_TIMEOUT_SECONDS) -> Optional[str]:
    """轮询临时邮箱等待验证码邮件"""
    start = time.time()
    seen_ids = set()
    print(f"[*] 开始轮询邮箱，等待验证码，超时时间: {timeout} 秒")

    while time.time() - start < timeout:
        messages = fetch_emails(mail_token)
        if messages:
            print(f"[*] 发现 {len(messages)} 封邮件")
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            subject = msg.get("subject", "")
            sender = msg.get("from", "")
            print(f"[*] 邮件 ID: {msg_id}, 主题: {subject}, 发件人: {sender}")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            # parsed_mails 已经包含 text/html，先直接尝试
            content = msg.get("text") or msg.get("html") or ""
            if content:
                code = extract_verification_code(content)
                if code:
                    print(f"[*] 从邮件列表提取到验证码: {code}")
                    return code
                else:
                    print(f"[*] 从邮件正文提取验证码失败，正文长度: {len(content)}")

            # 如果列表里没有内容，拉详情
            detail = fetch_email_detail(mail_token, str(msg_id))
            if detail:
                content = detail.get("text") or detail.get("html") or ""
                code = extract_verification_code(content)
                if code:
                    print(f"[*] 从邮件详情提取到验证码: {code}")
                    return code
                else:
                    print(f"[Debug] 邮件详情正文提取验证码也失败，正文长度: {len(content)}")
        time.sleep(MAIL_POLL_INTERVAL_SECONDS)
    print("[*] 轮询超时，未收到或未解析出验证码")
    return None


def extract_verification_code(content: str) -> Optional[str]:
    """
    从邮件内容提取验证码。
    Grok/x.ai 格式：MM0-SF3（3位-3位字母数字混合）或 6 位纯数字。
    """
    if not content:
        return None

    # 模式 1: Grok 格式 XXX-XXX
    m = re.search(r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])", content)
    if m:
        return m.group(1)

    # 模式 2: 带标签的验证码
    m = re.search(r"(?:verification code|验证码|your code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b", content, re.IGNORECASE)
    if m:
        return m.group(1)

    # 模式 3: HTML 样式包裹
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>", content)
    if m:
        return m.group(1)

    # 模式 4: Subject 行 6 位数字
    m = re.search(r"Subject:.*?(\d{6})", content)
    if m and m.group(1) != "177010":
        return m.group(1)

    # 模式 5: HTML 标签内 6 位数字
    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code

    # 模式 6: 独立 6 位数字
    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code

    return None
