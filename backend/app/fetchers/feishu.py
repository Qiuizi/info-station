"""飞书（官方开放平台）接入骨架 —— 尚未跑通，详见 docs/feishu.md。

思路：企业自建应用 + 机器人能力 + 订阅 im.message.receive_v1，用官方 SDK lark-oapi 的
**长连接（WebSocket）** 模式在本机接收事件（无需公网地址），把指定群/会话的消息写入
本地 /api/ingest/items（与其他信源走同一条筛选流水线）。

隐私：聊天内容默认 **不** 送往云端模型（category=feishu 时 pipeline 只用规则打分），
除非在 sources.yaml 该信源里显式设置 allow_llm: true。
"""
from __future__ import annotations

import os
from typing import Any

from .base import FetchContext, FetchError, RawItem


async def fetch_feishu(source: dict[str, Any], ctx: FetchContext) -> list[RawItem]:
    # 飞书是“推”模型：消息通过长连接实时进来，不需要定时拉取。
    raise FetchError("飞书信源由长连接推送（python -m app.fetchers.feishu），无需定时抓取")


def run_long_connection() -> None:  # pragma: no cover - 需要真实飞书应用
    try:
        import lark_oapi as lark
    except ImportError as e:
        raise SystemExit("请先安装：uv sync --extra feishu") from e
    import httpx

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    allow_chats = {c for c in os.environ.get("FEISHU_CHAT_IDS", "").split(",") if c}
    backend = os.environ.get("INFO_STATION_URL", "http://127.0.0.1:8765")
    token = os.environ.get("INFO_STATION_TOKEN", "")
    if not app_id or not app_secret:
        raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量")

    def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        msg = data.event.message
        if allow_chats and msg.chat_id not in allow_chats:
            return
        import json as _json

        try:
            text = _json.loads(msg.content).get("text", "")
        except Exception:  # noqa: BLE001
            text = msg.content
        httpx.post(
            f"{backend}/api/ingest/items",
            headers={"X-Local-Token": token} if token else {},
            json={"source_id": "feishu", "items": [{
                "title": text[:60] or "(飞书消息)",
                "url": f"feishu://chat/{msg.chat_id}/{msg.message_id}",
                "content": text,
            }]},
            timeout=10,
        )

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    cli = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    cli.start()


if __name__ == "__main__":  # pragma: no cover
    run_long_connection()
