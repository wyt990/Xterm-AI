"""
代理工具：判断目标是否为本地/局域网，用于 ignore_local 场景跳过代理
"""
import re
from urllib.parse import urlparse


def host_from_url(url: str) -> str:
    """从 URL 提取 host，用于 AI base_url、技能 GitHub 等"""
    if not url or not url.strip():
        return ""
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        parsed = urlparse(u)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def is_local_host(host: str) -> bool:
    """
    判断 host 是否为本地或局域网地址，访问此类地址时可直连不经过代理。
    支持：10.x、127.x、192.168.x、172.16–31.x、localhost、169.254.x（链路本地）
    """
    if not host or not host.strip():
        return True
    h = host.strip().lower()
    # localhost
    if h in ("localhost", "127.0.0.1", "::1"):
        return True
    # 移除端口（如有）
    if ":" in h and "]" not in h and not h.startswith("["):
        h = h.split(":")[0]
    elif "]" in h:
        # IPv6 [::1]:443 形式
        m = re.match(r"\[([^\]]+)\]", h)
        if m:
            h = m.group(1)
    # IPv4 私有/本地
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", h)
    if m:
        a, b, c, d = (int(x) for x in m.groups())
        if any(x > 255 for x in (a, b, c, d)):
            return False
        if a == 10:
            return True
        if a == 127:
            return True
        if a == 192 and b == 168:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 169 and b == 254:
            return True
    # IPv6 本地
    if h in ("::1", "::"):
        return True
    if h.startswith("fe80:"):
        return True
    return False


def build_proxy_url(proxy: dict) -> str:
    """根据代理配置构造 URL，供 httpx 使用。格式: http://[user:pass@]host:port 或 socks5://..."""
    if not proxy or not proxy.get("host"):
        return ""
    scheme = "socks5" if (proxy.get("type") or "").lower() == "socks5" else "http"
    host = proxy["host"]
    port = int(proxy.get("port") or 0)
    if port <= 0:
        return ""
    user = proxy.get("username") or ""
    passwd = proxy.get("password") or ""
    if user and passwd:
        from urllib.parse import quote
        auth = f"{quote(user, safe='')}:{quote(passwd, safe='')}@"
        return f"{scheme}://{auth}{host}:{port}"
    return f"{scheme}://{host}:{port}"


def should_skip_proxy(proxy: dict, target_host: str) -> bool:
    """
    当 proxy.ignore_local 为真且 target_host 为本地/局域网时，应跳过代理。
    target_host 可为 IP、hostname 或 URL（会提取 host）。
    """
    if not proxy or not proxy.get("ignore_local"):
        return False
    host = host_from_url(target_host) if "://" in (target_host or "") else (target_host or "").strip()
    return is_local_host(host)
