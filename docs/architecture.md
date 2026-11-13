# 架构

```
Tauri 2 壳（无边框窗口 / 托盘 / 开机自启 / 单实例 / Windows Toast / 拉起 sidecar）
  └─ React + Vite 前端 ── HTTP 127.0.0.1:8765 ──► FastAPI 后端（sidecar）
                                                ├─ scheduler.py（APScheduler：信源间隔抓取、X 每日一次、
                                                │   每日谷时批量打分、关机 / 休眠后补抓）
                                                ├─ fetchers/（rss · html_list · json · github · playwright · x · feishu）
                                                ├─ pipeline/（runner：判重 → 归并 → 规则 → 即时提醒；
                                                │   batch：每日谷时 DeepSeek 批量打分 → 简报 → 汇总通知）
                                                └─ SQLite（WAL + FTS5 trigram）
```

硬规则（参考 AIHOT 的架构约束）：

1. 前端只通过 HTTP 读后端；数据库、模型、密钥只在后端。
2. **打开页面不触发模型调用**：模型只在每日谷时的批量打分里跑（或你手动点「立即打分」）；简报是纯规则编排。
3. 后端只监听 `127.0.0.1`；可选 `INFO_STATION_TOKEN` 做本机 token。
4. 模型调用有每日上限；401/402/403（Key 无效 / 余额不足）触发 30 分钟熔断，期间用规则分。
5. 聊天类信源（飞书）默认不送云端模型（`allow_llm: false`）。

## 主要表

| 表 | 用途 |
| --- | --- |
| `items` | 条目：原始字段、规则分、模型分（JSON 数组）、最终分、摘要、标签、截止时间、行动、是否相关、已读/收藏/反馈、事件 `cluster_id` |
| `items_fts` | FTS5（trigram 分词，中文可用；不足 3 个字的搜索自动退回 LIKE） |
| `source_state` | 每个信源的最近运行、成功、错误、计数 |
| `notifications` | 待发通知队列（前端轮询后发 Windows Toast 再 ack） |
| `briefs` | 每日简报快照 |
| `llm_usage` | 每日调用次数与 token |

## 主要接口

`GET /api/items`、`GET/PATCH /api/items/{id}`、`POST /api/items/mark-read`、`GET /api/deadlines`、
`GET/POST/PUT/DELETE /api/sources`、`POST /api/sources/{id}/run`、`POST /api/sources/run-all`、`POST /api/sources/preview`、
`POST /api/browser/login`、`GET /api/x/status`、`POST /api/x/login`、`POST /api/x/clear-block`、`GET /api/score/schedule`、`GET /api/briefs/today`、`GET/PUT /api/settings`、`POST /api/settings/test-llm`、
`GET /api/notifications/pending`、`POST /api/ingest/items`（外部推送，飞书长连接也走这里）。

完整文档：后端运行后打开 http://127.0.0.1:8765/docs 。
