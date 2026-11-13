"""邮件信源：解析（GBK / HTML / 附件）、只读收取、UID 增量、XOAUTH2、Outlook 设备代码流程、示例信源、规则加分。"""
import asyncio
import base64
import json
import socket
import threading
import time
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
import pytest
import respx

from app import config, db, mail_oauth, vault
from app.fetchers import FetchError, mail
from app.pipeline import rules as rules_mod

SRC = {"id": "mail-t", "name": "测试邮箱", "type": "email", "provider": "scu", "address": "2025000000000@stu.scu.edu.cn",
       "tier": "T1", "category": "email", "enabled": True, "interval_minutes": 15}


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, svc, name):
        return self.store.get((svc, name))

    def set_password(self, svc, name, value):
        self.store[(svc, name)] = value

    def delete_password(self, svc, name):
        self.store.pop((svc, name), None)

    def get_keyring(self):
        return self


@pytest.fixture()
def kr(monkeypatch):
    k = FakeKeyring()
    monkeypatch.setattr(vault, "_backend", k)
    return k


def gbk_mail(subject="关于2026年寒假交换项目报名的通知", sender="教务处 <jwc@scu.edu.cn>", mid="<a1@scu.edu.cn>",
             date="Fri, 13 Nov 2026 09:30:00 +0800"):
    m = MIMEMultipart()
    m["Subject"] = Header(subject, "gb2312").encode()  # 头用 GB2312 编码字（含超出 GB2312 的字也能按 GB18030 解）
    m["From"] = str(Header("教务处", "gb2312")) + " <" + sender.split("<")[1]
    m["Date"] = date
    m["Message-ID"] = mid
    m.attach(MIMEText("同学们好：\n报名截止时间为 2026年11月20日 17:00。\n请登录系统填写。", "plain", "gb18030"))
    att = MIMEApplication(b"%PDF-1.4", Name="x.pdf")
    att.add_header("Content-Disposition", "attachment", filename=("gb18030", "", "报名表.pdf"))
    m.attach(att)
    return m.as_bytes()


def html_mail():
    m = MIMEText("<html><head><style>.a{color:red}</style></head><body><p>面试邀请</p><p>时间：周五 14:00</p>"
                 "<script>alert(1)</script></body></html>", "html", "utf-8")
    m["Subject"] = Header("面试邀请 · 某某科技", "utf-8").encode()
    m["From"] = "HR <hr@example.com>"
    m["Message-ID"] = "<h1@example.com>"
    return m.as_bytes()


# ---------------------------------------------------------------- 解析
def test_parse_gbk_message_and_attachment():
    p = mail.parse_message(gbk_mail())
    assert p["subject"] == "关于2026年寒假交换项目报名的通知"
    assert p["from_name"] == "教务处" and p["from_addr"] == "jwc@scu.edu.cn"
    assert "报名截止时间为 2026年11月20日" in p["body"]
    assert p["attachments"] == ["报名表.pdf"]
    assert p["date"].utcoffset().total_seconds() == 8 * 3600


def test_parse_html_only_strips_style_and_script():
    p = mail.parse_message(html_mail())
    assert p["subject"] == "面试邀请 · 某某科技"
    assert "面试邀请" in p["body"] and "时间：周五 14:00" in p["body"]
    assert "color" not in p["body"] and "alert" not in p["body"]


def test_raw_8bit_gbk_header():
    raw = "Subject: 测试通知\r\nFrom: a@b.com\r\n\r\n正文".encode("gbk")
    p = mail.parse_message(raw)
    assert p["subject"] == "测试通知"


def test_gmail_webmail_link():
    link = mail.webmail_link({"provider": "gmail"}, "<abc@mail.gmail.com>")
    assert link.startswith("https://mail.google.com/mail/u/0/#search/rfc822msgid%3Aabc%40mail.gmail.com")
    assert mail.webmail_link({"provider": "scu"}, "x") == "https://mail.stu.scu.edu.cn/"
    assert mail.webmail_link({"provider": "custom", "host": "imap.x.com"}, "x") == ""


# ---------------------------------------------------------------- 假 IMAP 服务器
class FakeIMAP:
    instances: list["FakeIMAP"] = []
    mailbox: dict[int, bytes] = {}
    uidvalidity = 7
    password = "secret-code"

    def __init__(self, host, port, proxy_url, timeout):
        self.host, self.port, self.proxy_url = host, port, proxy_url
        self.log: list[tuple] = []
        self.selected_readonly = None
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        self.log.append(("LOGIN", user))
        if pw != self.password:
            raise mail.imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"ok"]

    def authenticate(self, mech, cb):
        self.log.append(("AUTHENTICATE", mech, cb(b"")))
        return "OK", [b"ok"]

    def _simple_command(self, name, *args):
        self.log.append((name, *args))
        return "OK", [b"ok"]

    def select(self, name, readonly=False):
        self.log.append(("SELECT", name, readonly))
        self.selected_readonly = readonly
        return "OK", [str(len(self.mailbox)).encode()]

    def response(self, code):
        return code, [str(self.uidvalidity).encode()] if code == "UIDVALIDITY" else [None]

    def uid(self, cmd, *args):
        self.log.append(("UID", cmd, *args))
        if cmd == "SEARCH":
            if args[0] == "UID":
                lo = int(args[1].split(":")[0])
                hits = [u for u in self.mailbox if u >= lo] or [max(self.mailbox)]  # 和真服务器一样：n:* 至少返回最后一封
            else:
                hits = list(self.mailbox)
            return "OK", [" ".join(map(str, sorted(hits))).encode()]
        if cmd == "FETCH":
            u = int(args[0])
            assert "BODY.PEEK[]" in args[1]  # 不会标记已读
            raw = self.mailbox[u]
            return "OK", [(f"1 (UID {u} INTERNALDATE \"13-Nov-2026 09:30:00 +0800\" BODY[]<0> {{{len(raw)}}}".encode(), raw),
                          b")"]
        raise AssertionError(f"不允许的 UID 命令：{cmd}")

    def logout(self):
        self.log.append(("LOGOUT",))

    def __getattr__(self, name):  # store / expunge / copy / move / append 等一律不允许
        raise AssertionError(f"不允许调用 {name}")


@pytest.fixture()
def fake_imap(monkeypatch, tmp_env, kr):
    db.init_db()
    FakeIMAP.instances = []
    FakeIMAP.mailbox = {101: gbk_mail(), 102: html_mail()}
    FakeIMAP.uidvalidity = 7
    monkeypatch.setattr(mail, "_IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(mail, "_IMAP4", FakeIMAP)
    vault.set(mail.secret_key(SRC["id"]), FakeIMAP.password)
    return FakeIMAP


def test_fetch_readonly_and_incremental(fake_imap, tmp_env):
    items = mail.fetch_sync(SRC, tmp_env)
    conn = fake_imap.instances[-1]
    assert conn.host == "mail.stu.scu.edu.cn" and conn.port == 993 and conn.proxy_url is None
    assert conn.selected_readonly is True
    assert ("LOGIN", SRC["address"]) in conn.log and conn.log[-1] == ("LOGOUT",)
    assert any(e[0] == "ID" for e in conn.log)  # Coremail 要求的 ID 命令
    assert [e for e in conn.log if e[:2] == ("UID", "SEARCH")][0][2] == "SINCE"  # 首次按天数
    assert [i.title for i in items] == ["关于2026年寒假交换项目报名的通知", "面试邀请 · 某某科技"]
    it = items[0]
    assert it.author == "教务处 <jwc@scu.edu.cn>" and "附件：报名表.pdf" in it.content
    assert it.extra["dedup_key"] == f"mail:{SRC['address']}:<a1@scu.edu.cn>"
    assert it.extra["attachments"] == ["报名表.pdf"] and it.url == "https://mail.stu.scu.edu.cn/"
    assert db.get_state("email_uid:mail-t") == {"INBOX": {"v": 7, "last": 102}}

    # 第二轮：没有新邮件 → 0 条；来一封新的 → 只取新的
    assert mail.fetch_sync(SRC, tmp_env) == []
    s2 = [e for e in fake_imap.instances[-1].log if e[:2] == ("UID", "SEARCH")][0]
    assert s2[2:4] == ("UID", "103:*")
    fake_imap.mailbox[103] = gbk_mail(subject="面试安排", mid="<a3@scu.edu.cn>")
    assert [i.title for i in mail.fetch_sync(SRC, tmp_env)] == ["面试安排"]
    # UIDVALIDITY 变了：重新按天数取（入库时按 Message-ID 去重）
    fake_imap.uidvalidity = 8
    assert len(mail.fetch_sync(SRC, tmp_env)) == 3


def test_max_per_run_takes_newest(fake_imap, tmp_env):
    fake_imap.mailbox = {i: gbk_mail(subject=f"邮件{i}", mid=f"<m{i}@x>") for i in range(1, 11)}
    items = mail.fetch_sync({**SRC, "max_per_run": 3}, tmp_env)
    assert [i.title for i in items] == ["邮件8", "邮件9", "邮件10"]


def test_errors_are_clear(fake_imap, tmp_env):
    with pytest.raises(FetchError, match="地址还没填好"):
        mail.fetch_sync({**SRC, "address": "学号@stu.scu.edu.cn"}, tmp_env)
    vault.delete(mail.secret_key(SRC["id"]))
    with pytest.raises(FetchError, match="还没有保存客户端专用密码"):
        mail.fetch_sync(SRC, tmp_env)
    vault.set(mail.secret_key(SRC["id"]), "wrong")
    with pytest.raises(FetchError, match="登录失败：\\[AUTHENTICATIONFAILED\\]"):
        mail.fetch_sync(SRC, tmp_env)
    assert db.get_state("email_uid:mail-t") is None  # 失败不推进游标


def test_gmail_uses_proxy_and_outlook_uses_xoauth2(fake_imap, tmp_env, monkeypatch):
    tmp_env.fetch.proxy_url = "http://127.0.0.1:7897"
    g = {**SRC, "id": "mail-g", "provider": "gmail", "address": "me@gmail.com", "use_proxy": True}
    vault.set(mail.secret_key("mail-g"), FakeIMAP.password)
    mail.test_connection(g, tmp_env)
    assert fake_imap.instances[-1].host == "imap.gmail.com"
    assert fake_imap.instances[-1].proxy_url == "http://127.0.0.1:7897"

    o = {**SRC, "id": "mail-o", "provider": "outlook", "address": "me@outlook.com", "client_id": "cid"}
    monkeypatch.setattr(mail_oauth, "access_token", lambda src: "AT-123")
    mail.test_connection(o, tmp_env)
    conn = fake_imap.instances[-1]
    assert conn.host == "outlook.office365.com"
    auth = [e for e in conn.log if e[0] == "AUTHENTICATE"][0]
    assert auth[1] == "XOAUTH2" and auth[2] == b"user=me@outlook.com\x01auth=Bearer AT-123\x01\x01"


async def test_run_source_end_to_end(fake_imap, tmp_env, monkeypatch):
    from app.pipeline import runner

    monkeypatch.setattr(config, "load_sources", lambda: [SRC])
    r = await runner.run_source("mail-t")
    assert r["ok"] and r["new"] == 2
    with db.get_conn() as c:
        rows = c.execute("SELECT title, category, author, reason FROM items ORDER BY id").fetchall()
    assert rows[0]["category"] == "email" and "发件人:scu.edu.cn" in rows[0]["reason"]
    fake_imap.uidvalidity = 9  # 游标重置后再收一遍：按 Message-ID 去重，不会重复入库
    assert (await runner.run_source("mail-t"))["new"] == 0


# ---------------------------------------------------------------- 规则
def test_rules_sender_and_category_boost(tmp_env):
    base = rules_mod.apply_rules("周五例会", "", "email", "T2", tmp_env, ref=None, author="张三 <z@qq.com>").score
    scu = rules_mod.apply_rules("周五例会", "", "email", "T2", tmp_env, ref=None, author="教务处 <a@stu.scu.edu.cn>")
    assert scu.score == base + 15 and "+15 发件人:scu.edu.cn" in scu.hits
    iv = rules_mod.apply_rules("面试邀请", "", "email", "T2", tmp_env, ref=None, author="hr@x.com")
    assert iv.score >= base + 12
    # 不是邮件分类：发件人与邮件专属加分词都不生效
    assert rules_mod.apply_rules("面试邀请", "", "news", "T2", tmp_env, ref=None, author="a@scu.edu.cn").hits == []
    fake_scu = rules_mod.apply_rules("x", "", "email", "T2", tmp_env, ref=None, author="a@evilscu.edu.cn")
    assert fake_scu.score == base


# ---------------------------------------------------------------- 示例信源
def test_example_sources_seeded_once(tmp_env, monkeypatch, tmp_path):
    db.init_db()
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    p = tmp_path / "sources.yaml"
    p.write_text("# 我的注释\nsources:\n  - id: a\n    type: rss\n    url: u  # 行尾注释\n", encoding="utf-8")
    added = config.ensure_example_sources(mail.EXAMPLES, "seeded:test")
    assert added == ["mail-scu", "mail-qq", "mail-gmail", "mail-outlook"]
    text = p.read_text(encoding="utf-8")
    assert "# 我的注释" in text and "# 行尾注释" in text
    srcs = config.load_sources()
    assert [s["id"] for s in srcs] == ["a", "mail-scu", "mail-qq", "mail-gmail", "mail-outlook"]
    assert all(not s["enabled"] for s in srcs[1:]) and all(s["category"] == "email" for s in srcs[1:])
    assert not any("secret" in s or "password" in s for s in srcs)
    config.save_sources([srcs[0]])  # 你删掉了示例
    assert config.ensure_example_sources(mail.EXAMPLES, "seeded:test") == []
    assert [s["id"] for s in config.load_sources()] == ["a"]


def test_example_sources_with_unindented_list(tmp_env, monkeypatch, tmp_path):
    db.init_db()
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "sources.yaml").write_text("sources:\n- id: a\n  type: rss\n", encoding="utf-8")
    config.ensure_example_sources(mail.EXAMPLES[:1], "seeded:t2")
    assert [s["id"] for s in config.load_sources()] == ["a", "mail-scu"]


# ---------------------------------------------------------------- HTTP 代理隧道
def _proxy_server(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = {}

    def run():
        c, _ = srv.accept()
        seen["req"] = c.recv(4096)
        c.sendall(reply)
        seen["after"] = c.recv(16)
        c.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], seen


def test_tunnel_connect():
    port, seen = _proxy_server(b"HTTP/1.1 200 Connection established\r\n\r\n")
    s = mail._tunnel(f"http://u:p@127.0.0.1:{port}", "imap.gmail.com", 993, 5)
    s.sendall(b"ping")
    time.sleep(0.2)
    s.close()
    assert seen["req"].startswith(b"CONNECT imap.gmail.com:993 HTTP/1.1\r\n")
    assert b"Proxy-Authorization: Basic " + base64.b64encode(b"u:p") in seen["req"]
    assert seen["after"] == b"ping"


def test_tunnel_refused():
    port, _ = _proxy_server(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    with pytest.raises(FetchError, match="代理拒绝连接"):
        mail._tunnel(f"http://127.0.0.1:{port}", "imap.gmail.com", 993, 5)
    with pytest.raises(FetchError, match="只支持 HTTP 代理"):
        mail._tunnel("socks5://127.0.0.1:1080", "imap.gmail.com", 993, 5)


# ---------------------------------------------------------------- Outlook OAuth（设备代码）
OSRC = {"id": "mail-o", "type": "email", "provider": "outlook", "address": "me@outlook.com", "client_id": "my-client"}
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
DEVICE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"


def test_oauth_requires_client_id(tmp_env, kr):
    with pytest.raises(mail_oauth.OAuthError, match="client_id"):
        asyncio.run(mail_oauth.start({**OSRC, "client_id": ""}))


@respx.mock
async def test_device_code_flow_stores_refresh_token(tmp_env, kr, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(mail_oauth.asyncio, "sleep", lambda s: real_sleep(0))
    respx.post(DEVICE_URL).mock(return_value=httpx.Response(200, json={
        "device_code": "DC", "user_code": "ABCD-EFGH", "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900, "interval": 5}))
    tok = respx.post(TOKEN_URL).mock(side_effect=[
        httpx.Response(400, json={"error": "authorization_pending"}),
        httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}),
    ])
    st = await mail_oauth.start(OSRC)
    assert st["user_code"] == "ABCD-EFGH" and st["pending"]
    await mail_oauth._tasks["mail-o"]
    assert tok.call_count == 2
    assert b"scope=" not in tok.calls[0].request.content and b"device_code=DC" in tok.calls[0].request.content
    assert mail_oauth.status("mail-o")["authorized"] and mail_oauth.status("mail-o")["state"] == "done"
    saved = json.loads(vault.get("email-oauth:mail-o"))
    assert saved["refresh_token"] == "RT1"
    assert mail_oauth.access_token(OSRC) == "AT1"  # 未过期：直接用


@respx.mock
def test_access_token_refreshes_and_rolls(tmp_env, kr):
    vault.set("email-oauth:mail-o", json.dumps({"refresh_token": "RT1", "access_token": "old", "expires_at": 0}))
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(
        200, json={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}))
    assert mail_oauth.access_token(OSRC) == "AT2"
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=RT1" in body and "IMAP.AccessAsUser.All" in body
    assert json.loads(vault.get("email-oauth:mail-o"))["refresh_token"] == "RT2"
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant",
                                                                        "error_description": "AADSTS70000: expired"}))
    vault.set("email-oauth:mail-o", json.dumps({"refresh_token": "RT2", "expires_at": 0}))
    with pytest.raises(mail_oauth.OAuthError, match="重新授权"):
        mail_oauth.access_token(OSRC)


def test_api_never_returns_secret(tmp_env, kr, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    db.init_db()
    monkeypatch.setattr(config, "load_sources", lambda: [SRC])
    monkeypatch.setattr(config, "get_source", lambda sid: dict(SRC) if sid == SRC["id"] else None)
    c = TestClient(main.app)
    assert c.get("/api/email/mail-t").json()["has_secret"] is False
    r = c.put("/api/email/mail-t/secret", json={"secret": "abcd efgh"})
    assert r.status_code == 200 and r.json()["has_secret"] is True and "abcd" not in r.text
    assert vault.get("email:mail-t") == "abcd efgh"  # 川大的专用密码不去空格
    assert "providers" in c.get("/api/email/providers").json()
    c.delete("/api/email/mail-t/secret")
    assert vault.get("email:mail-t") is None


def test_allow_llm_false_in_extra_keeps_rule_score(tmp_env, monkeypatch):
    """邮件信源 extra.allow_llm=false：只用规则分，不进模型队列。"""
    from app import config, db
    from app.pipeline import runner

    src = {"id": "m1", "name": "m", "type": "email", "category": "email", "tier": "T2", "enabled": True,
           "extra": {"provider": "qq", "address": "a@qq.com", "allow_llm": False}}
    monkeypatch.setattr(config, "load_sources", lambda: [src])
    monkeypatch.setattr(runner, "llm_active", lambda s: True)
    from app.config import load_settings
    from app.fetchers.base import RawItem

    ids = runner.insert_items(src, [RawItem(title="面试通知", url="mail://x", content="请明天来面试")], load_settings(), False)
    runner.apply_rules_to(ids, load_settings(), notify=False)
    with db.get_conn() as c:
        assert c.execute("SELECT status FROM items WHERE id=?", (ids[0],)).fetchone()[0] == "rule"
