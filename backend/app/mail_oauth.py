"""Outlook.com（个人微软账户）IMAP 的 OAuth2：设备代码流程 + XOAUTH2。

微软已关闭 Outlook.com 的 IMAP 基本认证（密码 / 应用密码都不行），只能用 OAuth2：

1. 你在 Microsoft Entra 注册一个应用（公共客户端，支持个人 Microsoft 账户，允许公共客户端流），
   把它的「应用程序(客户端) ID」填进信源的 ``client_id``——本项目不内置、也不冒用别人的 client id；
2. 点「Microsoft 授权」：后端向 ``login.microsoftonline.com/<tenant>/oauth2/v2.0/devicecode`` 申请设备代码，
   界面显示一个短码，你在浏览器打开 microsoft.com/devicelogin 输入短码并同意；
3. 后端轮询拿到 access_token + refresh_token，refresh_token 存进 Windows 凭据管理器（vault），
   之后每次抓取前用它换新的 access_token（微软会滚动刷新，新的 refresh_token 会写回）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from . import config, proxy, vault
from .textutil import now_iso

log = logging.getLogger("mail_oauth")

SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
AUTHORITY = "https://login.microsoftonline.com"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

_flows: dict[str, dict[str, Any]] = {}  # source_id -> 进行中的授权（只存在内存里，不含令牌）
_tasks: dict[str, asyncio.Task] = {}


class OAuthError(RuntimeError):
    pass


def vault_key(source_id: str) -> str:
    return f"email-oauth:{source_id}"


def _endpoints(source: dict[str, Any]) -> tuple[str, str]:
    tenant = (source.get("tenant") or "consumers").strip()
    base = f"{AUTHORITY}/{tenant}/oauth2/v2.0"
    return f"{base}/devicecode", f"{base}/token"


def _client_id(source: dict[str, Any]) -> str:
    cid = str(source.get("client_id") or "").strip()
    if not cid:
        raise OAuthError("还没有填 client_id：先在 Microsoft Entra 注册一个应用（见 README「Outlook」一节），把客户端 ID 填进信源")
    return cid


def _client_kwargs(source: dict[str, Any]) -> dict[str, Any]:
    purl, _ = proxy.resolve(config.load_settings(), source)
    return {"proxy": purl, "timeout": 20}


def _save_tokens(source_id: str, tok: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "refresh_token": tok.get("refresh_token") or (old or {}).get("refresh_token"),
        "access_token": tok.get("access_token"),
        "expires_at": time.time() + int(tok.get("expires_in") or 3600),
        "updated_at": now_iso(),
    }
    if not data["refresh_token"]:
        raise OAuthError("微软没有返回 refresh_token（scope 里需要 offline_access）")
    vault.set(vault_key(source_id), json.dumps(data))
    return data


def has_token(source_id: str) -> bool:
    return vault.has(vault_key(source_id))


def forget(source_id: str) -> None:
    vault.delete(vault_key(source_id))


def access_token(source: dict[str, Any]) -> str:
    """同步取可用的 access_token（抓取线程里调用）；过期就用 refresh_token 换新的。"""
    raw = vault.get(vault_key(source["id"]))
    if not raw:
        raise OAuthError("还没有完成 Microsoft 授权：在信源卡片里点「Microsoft 授权」")
    data = json.loads(raw)
    if data.get("access_token") and float(data.get("expires_at") or 0) > time.time() + 120:
        return data["access_token"]
    _, token_url = _endpoints(source)
    with httpx.Client(**_client_kwargs(source)) as c:
        r = c.post(token_url, data={"client_id": _client_id(source), "grant_type": "refresh_token",
                                    "refresh_token": data["refresh_token"], "scope": SCOPE})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "access_token" not in body:
        err = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
        raise OAuthError(f"Microsoft 授权已失效，请重新授权（{str(err).splitlines()[0][:160]}）")
    return _save_tokens(source["id"], body, data)["access_token"]


def status(source_id: str) -> dict[str, Any]:
    flow = _flows.get(source_id) or {}
    return {
        "authorized": has_token(source_id),
        "pending": flow.get("state") == "pending",
        "state": flow.get("state"),  # pending / done / error / expired
        "user_code": flow.get("user_code") if flow.get("state") == "pending" else None,
        "verification_uri": flow.get("verification_uri"),
        "expires_at": flow.get("expires_at"),
        "message": flow.get("message"),
    }


async def start(source: dict[str, Any]) -> dict[str, Any]:
    """申请设备代码并在后台轮询；返回给界面展示的短码。"""
    sid = source["id"]
    device_url, token_url = _endpoints(source)
    cid = _client_id(source)
    async with httpx.AsyncClient(**_client_kwargs(source)) as c:
        r = await c.post(device_url, data={"client_id": cid, "scope": SCOPE})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "device_code" not in body:
        err = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
        raise OAuthError(f"申请设备代码失败：{str(err).splitlines()[0][:200]}")
    flow = {
        "state": "pending",
        "user_code": body["user_code"],
        "verification_uri": body.get("verification_uri") or "https://microsoft.com/devicelogin",
        "expires_at": time.time() + int(body.get("expires_in") or 900),
        "interval": int(body.get("interval") or 5),
        "message": "在浏览器打开链接，输入短码并用你的 Outlook 账号同意授权",
    }
    _flows[sid] = flow
    old = _tasks.pop(sid, None)
    if old and not old.done():
        old.cancel()
    _tasks[sid] = asyncio.create_task(_poll(source, token_url, cid, body["device_code"], flow))
    return status(sid)


async def _poll(source: dict[str, Any], token_url: str, cid: str, device_code: str, flow: dict[str, Any]) -> None:
    interval = flow["interval"]
    async with httpx.AsyncClient(**_client_kwargs(source)) as c:
        while time.time() < flow["expires_at"]:
            await asyncio.sleep(interval)
            try:
                r = await c.post(token_url, data={"client_id": cid, "grant_type": DEVICE_GRANT, "device_code": device_code})
                body = r.json()
            except Exception as e:  # noqa: BLE001 — 网络抖动，下一轮再试
                log.warning("轮询 Microsoft 令牌失败：%s", e)
                continue
            err = body.get("error")
            if not err and body.get("access_token"):
                try:
                    _save_tokens(source["id"], body)
                    flow.update(state="done", message="授权成功，令牌已存进 Windows 凭据管理器")
                except Exception as e:  # noqa: BLE001
                    flow.update(state="error", message=str(e))
                return
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            msg = {"authorization_declined": "你拒绝了授权", "expired_token": "短码已过期，请重新开始",
                   "bad_verification_code": "短码无效"}.get(err, body.get("error_description") or err)
            flow.update(state="error", message=str(msg).splitlines()[0][:200])
            return
    flow.update(state="expired", message="短码已过期，请重新开始")
