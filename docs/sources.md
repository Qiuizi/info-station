# 信源

## 写法

```yaml
- id: sw-xytz                  # 小写英文、数字、-、_
  name: 软件学院 · 学院通知
  type: playwright             # rss / html_list / json / github / playwright / feishu
  url: https://sw.scu.edu.cn/xwgg/xytz.htm
  tier: T1                     # T1 官方一手；T2 媒体/聚合（门槛 +10）
  category: school             # school / competition / tech / news
  enabled: true
  interval_minutes: 60         # 校内站点建议 ≥ 30
  wait_for: span.fr            # playwright：等这个元素出现再解析
  list:
    item: "li:has(span.fr)"    # 每一条
    title: a@title             # sel 取文本；sel@attr 取属性；@attr 取 item 自身属性
    link: a                    # 默认 item 自身（<a>）或其中第一个 a
    date: span.fr              # 可写列表，按“年 月 日”顺序拼接后宽松解析
    limit: 15
  detail:                      # 可选：补抓正文（用于截止日期与模型打分）
    enabled: true
    title: h1                  # 列表标题被截成“...”时用详情页标题补全
    content: [.v_news_content, "#vsb_content"]
    max: 4                     # 每轮最多补抓几篇（只补新条目）
```

- `rss`：`url` 可写 `{RSSHUB}/路由`，`{RSSHUB}` 取设置里的 RSSHub 地址（默认 `http://127.0.0.1:1200`，需要自己部署 RSSHub）。
- `json`：`items_path`（点路径指向数组）+ `fields: {title, url, date, summary}`，或 `url_template`。
- `github`：`mode: releases`（`repo: owner/name`）、`mode: search`（`query`，支持 `{days_ago:7}`）、`mode: trending`（`language`、`since`）。
- 新增信源前，在编辑弹窗里点 **试抓预览**，确认标题、链接、日期都对再保存。

## 预置信源实测（2026-11-11，box 上真实抓取入库）

| 信源 | 类型 | 结果 | 备注 |
| --- | --- | --- | --- |
| 四川大学 · 通知公告 `www.scu.edu.cn/index/xw/tzgg.htm` | html_list | ✅ 10 条，正文补抓 5/6 | 静态页 |
| 川大教务处 · 通知公告 `jwc.scu.edu.cn/tzgg.htm` | html_list | ✅ 12 条，正文 8/8 | 列表标题截断，用详情页 h1 补全 |
| 软件学院 · 学院通知 `sw.scu.edu.cn/xwgg/xytz.htm` | playwright | ✅ 15 条 | 站点有瑞数 JS 反爬（HTTP 202），httpx 抓不到，必须浏览器渲染 |
| 软件学院 · 教学运行 `sw.scu.edu.cn/jxgl.htm` | playwright | ✅ 15 条 | 同上 |
| 软件学院 · 科技创新与竞赛 `sw.scu.edu.cn/xsgz/kjcx.htm` | playwright | ✅ 15 条 | 同上；该栏目以成果报道为主 |
| 四川大学新闻网 · 综合新闻 `news.scu.edu.cn/zhxw.htm` | html_list | ✅ 10 条 | 新闻网偶发 500，有重试 |
| CCF CSP 认证 · 通知 `cspro.org` | html_list | ✅ 5 条 | 详情页是跳转模板，正文为空，只用标题 |
| AIHOT · AI 精选 `aihot.news/feed.xml` | rss | ✅ 30 条 | 个人非商业订阅 |
| GitHub Trending · Python | github | ✅ 10 条 | 非官方页面，结构变了会报错但不影响其他源 |
| GitHub · 本周新晋 AI 仓库（搜索 API） | github | ✅ 3 条 | 无 token 时 10 次/分钟限额 |
| Tauri / uv Releases | github | ✅ 各 5 条 | |
| Hacker News · 200+ 分（hnrss） | rss | ✅ 13 条 | |
| 中新网 · 即时新闻 | rss | ✅ 30 条 | |
| IT之家 | rss | ✅ 30 条 | |
| X 话题 · DeepSeek（RSSHub） | rss | ⏸ 默认停用 | 需要自建 RSSHub 并配置 X 的 cookie，路由常不稳定 |
| 飞书 · 指定群消息 | feishu | ⏸ 骨架 | 见 docs/feishu.md |

说明：预置信源里的软件学院官网 `sw.scu.edu.cn` 页面上显示的单位名是“计算机学院（软件学院）”，栏目列表结构一致。
如果以后学院改版，先在「信源管理 → 编辑 → 试抓预览」里调整选择器。
