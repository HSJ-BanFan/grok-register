
from __future__ import annotations

import json
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

# ============================================================
# 适配层：为 DrissionPage_example.py 提供简单接口
# ============================================================

_temp_email_cache: Dict[str, str] = {}


def get_email_and_token() -> Tuple[Optional[str], Optional[str]]:
    """
    创建临时邮箱并返回 (email, jwt_token)。
    供 DrissionPage_example.py 调用。
    """
    email, _password, jwt_token = create_temp_email()
    if email and jwt_token:
        _temp_email_cache[email] = jwt_token
        return email, jwt_token
    return None, None


def get_oai_code(dev_token: str, email: str, timeout: int = VERIFICATION_TIMEOUT_SECONDS) -> Optional[str]:
    """
    轮询临时邮箱获取 OTP 验证码。
    供 DrissionPage_example.py 调用。

    Returns:
        验证码字符串（去除连字符，如 "MM0SF3"）或 None
    """
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


def wait_for_verification_code(mail_token: str, timeout: int = VERIFICATION_TIMEOUT_SECONDS) -> Optional[str]:
    """轮询临时邮箱等待验证码邮件"""
    start = time.time()
    seen_ids = set()

    while time.time() - start < timeout:
        messages = fetch_emails(mail_token)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
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

            # 如果列表里没有内容，拉详情
            detail = fetch_email_detail(mail_token, str(msg_id))
            if detail:
                content = detail.get("text") or detail.get("html") or ""
                code = extract_verification_code(content)
                if code:
                    print(f"[*] 从邮件详情提取到验证码: {code}")
                    return code
        time.sleep(MAIL_POLL_INTERVAL_SECONDS)
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
