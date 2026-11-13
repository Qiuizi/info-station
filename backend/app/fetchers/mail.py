"""邮件信源（type: email）：IMAP 只读收取，进入规则分 + 每日谷时模型打分，分类「邮件」。

只读保证：
- ``SELECT`` 用 readonly（EXAMINE），取信用 ``BODY.PEEK[]``，不会把邮件标成已读；
- 不发送、不删除、不移动、不改标记（代码里没有 STORE / EXPUNGE / COPY / MOVE / APPEND）。

增量：每个文件夹记住 UIDVALIDITY + 已处理的最大 UID（存在本地数据库 state 表）；首次只看最近 N 天（默认 7），
每轮最多取 max_per_run 封（默认 50，取最新的）。正文只取前 1 MB 并截断到 8000 字；附件只记文件名。

认证：
- password：邮箱的「授权码 / 客户端专用密码 / 应用专用密码」，存在 Windows 凭据管理器（app/vault.py）；
- oauth2_ms：Outlook.com 个人账户，XOAUTH2（令牌流程见 app/mail_oauth.py）。

Gmail / Outlook 在国内通常需要代理：IMAP 走设置里的 HTTP 代理（HTTP CONNECT 隧道），信源 use_proxy 控制。
"""
from __future__ import annotations

import asyncio
import base64
import email
import imaplib
import logging
import re
import socket
import ssl
from datetime import datetime, timedelta
from email.header import Header, decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .. import config, db, mail_oauth, proxy, vault
from ..textutil import TZ, clean_text, decode_bytes, now
from .base import FetchContext, FetchError, RawItem

log = logging.getLogger("mail")

# ---------------------------------------------------------------- 预设
PROVIDERS: dict[str, dict[str, Any]] = {
    "qq": {
        "label": "QQ 邮箱", "host": "imap.qq.com", "port": 993, "ssl": True, "auth": "password",
        "secret_label": "授权码", "use_proxy": False, "webmail": "https://wx.mail.qq.com/",
        "help": "QQ 邮箱网页版 → 设置 → 账号与安全 → 安全设置 → 开启「IMAP/SMTP 服务」，按提示发短信验证后生成 16 位授权码，"
                "把授权码（不是 QQ 密码）填在这里。",
    },
    "scu": {
        "label": "川大校园邮箱", "host": "mail.stu.scu.edu.cn", "port": 993, "ssl": True, "auth": "password",
        "secret_label": "客户端专用密码", "use_proxy": False, "webmail": "https://mail.stu.scu.edu.cn/",
        "help": "学生邮箱（学号@stu.scu.edu.cn）服务器 mail.stu.scu.edu.cn，教职工邮箱（@scu.edu.cn）改成 mail.scu.edu.cn。"
                "Coremail 网页邮箱 → 设置 → 账户与安全（或「客户端设置 / 密码」）→ 新增第三方客户端专用密码；"
                "开启了二次认证的账号必须用专用密码，没开也建议用。地址填完整邮箱。",
    },
    "gmail": {
        "label": "Gmail", "host": "imap.gmail.com", "port": 993, "ssl": True, "auth": "password",
        "secret_label": "应用专用密码", "use_proxy": True,
        "webmail": "https://mail.google.com/mail/u/0/#inbox",
        "help": "个人 Gmail：先开启两步验证，再到 myaccount.google.com/apppasswords 生成 16 位「应用专用密码」填这里"
                "（Gmail 的 IMAP 自 2025 年起始终开启）。Google Workspace（学校 / 公司）账号不支持应用密码，需要 OAuth，暂不支持。"
                "国内需要代理：保持「走代理」开启。",
    },
    "outlook": {
        "label": "Outlook / Hotmail", "host": "outlook.office365.com", "port": 993, "ssl": True, "auth": "oauth2_ms",
        "secret_label": "", "use_proxy": False, "webmail": "https://outlook.live.com/mail/0/",
        "help": "微软已关闭 Outlook.com 的 IMAP 密码登录（应用密码也不行），只能用 OAuth2：在 Microsoft Entra 注册一个应用"
                "（支持个人 Microsoft 账户、允许公共客户端流），把「应用程序(客户端) ID」填进信源 client_id，"
                "再点「Microsoft 授权」输入短码。另需在 Outlook.com 设置 → 邮件 → 转发和 IMAP 里打开「允许设备和应用使用 IMAP」。"
                "详见 README。",
    },
    "custom": {
        "label": "其他 IMAP", "host": "", "port": 993, "ssl": True, "auth": "password",
        "secret_label": "密码 / 授权码", "use_proxy": False, "webmail": "",
        "help": "填写邮箱服务商提供的 IMAP 服务器（一般是 imap.域名 或 mail.域名，SSL 端口 993）和授权码。",
    },
}

# 4 个默认停用的示例信源（首次启动时补进 sources.yaml，见 config.ensure_example_sources）
EXAMPLES: list[dict[str, Any]] = [
    {"id": "mail-scu", "name": "川大校园邮箱", "type": "email", "provider": "scu", "address": "学号@stu.scu.edu.cn",
     "tier": "T1", "category": "email", "enabled": False, "interval_minutes": 15},
    {"id": "mail-qq", "name": "QQ 邮箱", "type": "email", "provider": "qq", "address": "QQ号@qq.com",
     "tier": "T2", "category": "email", "enabled": False, "interval_minutes": 15},
    {"id": "mail-gmail", "name": "Gmail", "type": "email", "provider": "gmail", "address": "you@gmail.com",
     "tier": "T2", "category": "email", "enabled": False, "interval_minutes": 15, "use_proxy": True},
    {"id": "mail-outlook", "name": "Outlook", "type": "email", "provider": "outlook", "address": "you@outlook.com",
     "tier": "T2", "category": "email", "enabled": False, "interval_minutes": 15, "client_id": ""},
]

BODY_BYTES = 1024 * 1024
CONTENT_LIMIT = 8000


def preset(source: dict[str, Any]) -> dict[str, Any]:
    p = dict(PROVIDERS.get(source.get("provider") or "custom", PROVIDERS["custom"]))
    for k in ("host", "port", "ssl", "auth", "webmail"):
        if source.get(k) not in (None, ""):
            p[k] = source[k]
    p["port"] = int(p["port"])
    return p


def secret_key(source_id: str) -> str:
    return f"email:{source_id}"


def has_secret(source: dict[str, Any]) -> bool:
    if preset(source)["auth"] == "oauth2_ms":
        return mail_oauth.has_token(source["id"])
    return vault.has(secret_key(source["id"]))


def address_ok(addr: str) -> bool:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr or "") or re.search(r"[\u4e00-\u9fff]", addr):
        return False
    return addr.split("@")[0].lower() not in ("you", "your", "example")  # 示例信源里的占位地址


# ---------------------------------------------------------------- 连接（可走 HTTP 代理隧道）
def _tunnel(proxy_url: str, host: str, port: int, timeout: float) -> socket.socket:
    """通过 HTTP 代理的 CONNECT 建立到 host:port 的 TCP 隧道（Clash / v2rayN 的 HTTP 端口都支持）。"""
    u = urlparse(proxy_url)
    if u.scheme not in ("http", "https"):
        raise FetchError(f"邮件只支持 HTTP 代理（当前是 {u.scheme}），请在设置里填 http://127.0.0.1:端口")
    s = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
    req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if u.username:
        cred = base64.b64encode(f"{unquote(u.username)}:{unquote(u.password or '')}".encode()).decode()
        req += f"Proxy-Authorization: Basic {cred}\r\n"
    s.sendall((req + "\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    status = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    if not re.match(r"HTTP/1\.[01] 200", status):
        s.close()
        raise FetchError(f"代理拒绝连接 {host}:{port}：{status or '无响应'}")
    return s


class _IMAP4(imaplib.IMAP4):
    def __init__(self, host: str, port: int, proxy_url: str | None, timeout: float):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        if self._proxy_url:
            return _tunnel(self._proxy_url, self.host, self.port, timeout or 30)
        return super()._create_socket(timeout)


class _IMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, proxy_url: str | None, timeout: float):
        self._proxy_url = proxy_url
        super().__init__(host, port, ssl_context=ssl.create_default_context(), timeout=timeout)

    def _create_socket(self, timeout):
        if self._proxy_url:
            sock = _tunnel(self._proxy_url, self.host, self.port, timeout or 30)
            return self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        return super()._create_socket(timeout)


imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))


def _open(source: dict[str, Any], settings: config.Settings, timeout: float = 30):
    """连接并登录，返回 imaplib 连接。测试里会替换这个函数。"""
    p = preset(source)
    if not p["host"]:
        raise FetchError("没有填写 IMAP 服务器")
    addr = str(source.get("address") or "").strip()
    if not address_ok(addr):
        raise FetchError("邮箱地址还没填好（示例信源里的占位地址需要改成你自己的）")
    purl, _ = proxy.resolve(settings, source)
    cls = _IMAP4_SSL if p["ssl"] else _IMAP4
    try:
        conn = cls(p["host"], p["port"], purl, timeout)
    except FetchError:
        raise
    except (OSError, imaplib.IMAP4.error) as e:
        hint = "（国内连 Gmail 需要代理）" if "gmail" in p["host"] and not purl else ""
        raise FetchError(f"连不上 {p['host']}:{p['port']}：{e}{hint}") from e
    try:
        if p["auth"] == "oauth2_ms":
            token = mail_oauth.access_token(source)
            conn.authenticate("XOAUTH2", lambda _: f"user={addr}\x01auth=Bearer {token}\x01\x01".encode())
        else:
            secret = vault.get(secret_key(source["id"]))
            if not secret:
                raise FetchError(f"还没有保存{p.get('secret_label') or '密码'}：在信源卡片里填写后再试")
            conn.login(addr, secret)
    except mail_oauth.OAuthError as e:
        _logout(conn)
        raise FetchError(str(e)) from e
    except imaplib.IMAP4.error as e:
        _logout(conn)
        msg = _imap_text(e)
        raise FetchError(f"登录失败：{msg}。请检查地址和{p.get('secret_label') or '授权'}是否正确") from e
    try:  # 网易 / Coremail 系要求客户端先自报家门（RFC 2971 ID），否则可能拒绝 SELECT；不支持的服务器忽略
        conn._simple_command("ID", '("name" "InfoStation" "version" "0.1.0" "vendor" "InfoStation")')
    except Exception:  # noqa: BLE001
        pass
    return conn


def _logout(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


def _imap_text(e: Exception) -> str:
    s = str(e)
    m = re.match(r"^b['\"](.*)['\"]$", s)
    return (m.group(1) if m else s)[:200]


def _select(conn, folder: str) -> tuple[int, int]:
    """只读打开文件夹，返回 (UIDVALIDITY, 邮件数)。"""
    name = folder if re.fullmatch(r"[A-Za-z0-9_./-]+", folder) else '"' + folder.replace('"', '\\"') + '"'
    typ, data = conn.select(name, readonly=True)
    if typ != "OK":
        raise FetchError(f"打不开文件夹 {folder}：{_imap_text(Exception(data[0] if data else ''))}")
    count = int(data[0]) if data and data[0] and str(data[0], "ascii", "ignore").isdigit() else 0
    _, uv = conn.response("UIDVALIDITY")
    try:
        uidvalidity = int(uv[0]) if uv and uv[0] else 0
    except (TypeError, ValueError):
        uidvalidity = 0
    return uidvalidity, count


def test_connection(source: dict[str, Any], settings: config.Settings) -> dict[str, Any]:
    conn = _open(source, settings, timeout=20)
    try:
        uv, count = _select(conn, (source.get("folders") or ["INBOX"])[0])
        return {"ok": True, "count": count, "uidvalidity": uv, "host": preset(source)["host"]}
    finally:
        _logout(conn)


# ---------------------------------------------------------------- 解析
_GB = {"gb2312", "gbk", "gb_2312-80", "x-gbk", "cp936", "euc-cn", "hz-gb-2312"}


def _charset(cs: str | None) -> str:
    cs = (cs or "").strip().strip('"').lower()
    return "gb18030" if cs in _GB else cs


def decode_words(value: Any) -> str:
    """解码邮件头（RFC 2047 编码字，GB2312 按 GB18030 解；没编码的 8bit 头按 UTF-8 → GB18030 猜）。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return decode_bytes(value)
    if isinstance(value, Header):  # compat32 把含 8bit 字节的头包成 Header：直接拆块，别 str()（会变成 �）
        parts = decode_header(value)
    else:
        value = str(value)
        if re.search(r"[\udc80-\udcff]", value):  # 没编码的 8bit 头（常见于 GBK 老系统）：还原字节后再解
            value = decode_bytes(value.encode("ascii", "surrogateescape"))
        parts = decode_header(value)
    out = []
    for part, cs in parts:
        if isinstance(part, bytes):
            enc = _charset(cs) or "utf-8"
            try:
                out.append(part.decode(enc))
            except (LookupError, UnicodeDecodeError):
                out.append(decode_bytes(part))
        else:
            out.append(part)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _strip_html(html: str) -> str:
    try:
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser(html)
        tree.strip_tags(["style", "script", "head", "title"])
        return clean_text(tree.text(separator="\n", strip=True))
    except Exception:  # noqa: BLE001
        return clean_text(re.sub(r"<[^>]+>", " ", html))


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    return decode_bytes(payload, _charset(part.get_content_charset()))


def _filename(part: Message) -> str | None:
    name = part.get_filename()
    if not name:
        return None
    return decode_words(name) or None


def parse_message(raw: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    plain, html, attachments = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = _filename(part)
        disp = (part.get("Content-Disposition") or "").lower()
        if name or disp.startswith("attachment"):
            attachments.append(name or "未命名附件")
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html.append(_part_text(part))
    body = "\n".join(t for t in plain if t.strip()) or _strip_html("\n".join(html))
    from_name, from_addr = "", ""
    pairs = getaddresses([decode_words(msg.get("From"))])
    if pairs:
        from_name, from_addr = pairs[0][0].strip().strip('"'), pairs[0][1].strip().lower()
    try:
        date = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=TZ)
    except (TypeError, ValueError, IndexError):
        date = None
    return {
        "subject": decode_words(msg.get("Subject")) or "（无主题）",
        "from_name": decode_words(from_name),
        "from_addr": from_addr,
        "date": date.astimezone(TZ) if date else None,
        "message_id": (msg.get("Message-ID") or "").strip(),
        "body": clean_text(body, CONTENT_LIMIT),
        "attachments": attachments[:20],
    }


def webmail_link(source: dict[str, Any], message_id: str) -> str:
    p = preset(source)
    if (source.get("provider") == "gmail") and message_id:
        return "https://mail.google.com/mail/u/0/#search/" + quote("rfc822msgid:" + message_id.strip("<>"), safe="")
    if source.get("provider") == "scu" and p.get("host"):
        return f"https://{p['host']}/"
    return p.get("webmail") or ""


# ---------------------------------------------------------------- 抓取
def _state_key(source_id: str) -> str:
    return f"email_uid:{source_id}"


_FETCH_HEAD = re.compile(rb"UID (\d+)")
_INTERNAL = re.compile(rb'INTERNALDATE "([^"]+)"')


def _fetch_folder(conn, source: dict[str, Any], folder: str, st: dict[str, Any], days: int, cap: int) -> list[RawItem]:
    uidvalidity, count = _select(conn, folder)
    fst = st.get(folder) or {}
    last = int(fst.get("last") or 0) if fst.get("v") == uidvalidity else 0  # UIDVALIDITY 变了：从头按天数取
    since = (now() - timedelta(days=days)).strftime("%d-%b-%Y")
    if count == 0:
        st[folder] = {"v": uidvalidity, "last": last}
        return []
    crit = ["UID", f"{last + 1}:*", "SINCE", since] if last else ["SINCE", since]
    typ, data = conn.uid("SEARCH", *crit)
    if typ != "OK":
        raise FetchError(f"搜索 {folder} 失败：{_imap_text(Exception(data[0] if data else ''))}")
    uids = sorted({int(x) for x in (data[0] or b"").split() if x.isdigit()})
    uids = [u for u in uids if u > last][-cap:]  # 只要新邮件里最新的 cap 封
    items: list[RawItem] = []
    for uid in uids:
        typ, resp = conn.uid("FETCH", str(uid), f"(UID INTERNALDATE BODY.PEEK[]<0.{BODY_BYTES}>)")
        if typ != "OK" or not resp:
            continue
        raw, head = None, b""
        for part in resp:
            if isinstance(part, tuple) and len(part) >= 2:
                head, raw = part[0], part[1]
                break
        if raw is None:
            continue
        m = _FETCH_HEAD.search(head)
        if m and int(m.group(1)) != uid:
            continue
        info = parse_message(raw)
        date = info["date"]
        if date is None:
            mi = _INTERNAL.search(head)
            if mi:
                try:
                    date = datetime.strptime(mi.group(1).decode(), "%d-%b-%Y %H:%M:%S %z").astimezone(TZ)
                except ValueError:
                    date = None
        author = f"{info['from_name']} <{info['from_addr']}>" if info["from_name"] else info["from_addr"]
        att = info["attachments"]
        content = info["body"] + (("\n\n附件：" + "、".join(att)) if att else "")
        mailbox = str(source.get("address") or "").lower()
        items.append(RawItem(
            title=info["subject"][:300],
            url=webmail_link(source, info["message_id"]),
            published_at=date,
            summary=clean_text(info["body"], 300),
            content=content,
            author=author,
            extra={
                "dedup_key": f"mail:{mailbox}:{info['message_id'] or f'{folder}/{uidvalidity}/{uid}'}",
                "from_addr": info["from_addr"],
                "from_name": info["from_name"],
                "mailbox": mailbox,
                "folder": folder,
                "attachments": att,
            },
        ))
    st[folder] = {"v": uidvalidity, "last": max([last, *uids]) if uids else last}
    return items


def fetch_sync(source: dict[str, Any], settings: config.Settings) -> list[RawItem]:
    folders = [f for f in (source.get("folders") or ["INBOX"]) if str(f).strip()]
    days = max(1, int(source.get("days") or 7))
    cap = max(1, int(source.get("max_per_run") or 50))
    st = dict(db.get_state(_state_key(source["id"]), {}) or {})
    conn = _open(source, settings)
    items: list[RawItem] = []
    try:
        for folder in folders:
            items += _fetch_folder(conn, source, str(folder), st, days, cap)
    finally:
        _logout(conn)
    db.set_state(_state_key(source["id"]), st)  # 全部文件夹成功后才推进 UID 游标
    return items


def reset_state(source_id: str) -> None:
    db.set_state(_state_key(source_id), {})


async def fetch_email(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    return await asyncio.to_thread(fetch_sync, source, ctx.settings)
