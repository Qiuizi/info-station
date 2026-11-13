# 飞书接入（官方开放平台 · 骨架，未跑通）

只走飞书**官方开放平台**，不读客户端本地数据。

1. 在 https://open.feishu.cn 创建**企业自建应用**，开启「机器人」能力。
2. 权限：按需开通接收群聊 / 单聊消息相关权限（以控制台为准）。
3. 事件订阅：选择 **使用长连接接收事件**（本机无需公网地址），订阅 `im.message.receive_v1`。
4. 把机器人拉进要监控的群（很多场景需要 @ 机器人才能收到消息，取决于权限）。
5. 本机运行：
   ```powershell
   cd backend
   uv sync --extra feishu
   $env:FEISHU_APP_ID="cli_xxx"; $env:FEISHU_APP_SECRET="xxx"; $env:FEISHU_CHAT_IDS="oc_xxx"
   uv run python -m app.fetchers.feishu
   ```
   收到的消息会 POST 到本地 `/api/ingest/items`，`source_id = feishu`，和其他信源走同一条流水线。
6. 在 `config/sources.yaml` 把 `feishu` 信源 `enabled` 改成 `true`（它是推送型，不会被定时抓取）。

隐私：`allow_llm: false` 时飞书消息只用规则打分，不会发给 DeepSeek。

参考：
- 接收消息事件：https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
- 事件订阅概述：https://open.feishu.cn/document/server-docs/event-subscription-guide/overview
