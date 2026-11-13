from app.fetchers.html_list import extract_detail, parse_list
from app.fetchers.json_list import map_items
from app.fetchers.rss import parse_feed

JWC = """<ul>
<li><a href="info/1069/10920.htm"><div class="date"><p>11/04</p><span>2026</span></div>
<div class="text"><p>关于选派本科生参加寒假项目的通知...</p></div></a></li>
<li><a href="info/1069/10913.htm"><div class="date"><p>10/28</p><span>2026</span></div>
<div class="text"><p>关于期中考试的通知</p></div></a></li>
<li><a href="javascript:;">无效</a></li></ul>"""

SW = """<ul class="nav"><li><a href="xygk.htm" title="学院概况">学院概况</a></li></ul>
<ul><li id="line_u9_0"><span class="fr">2026-11-10</span><a href="../info/1038/15998.htm" title="芷云奖助学金评选通知">芷云奖...</a></li></ul>"""


def test_parse_list_jwc_style():
    items = parse_list(JWC, "https://jwc.scu.edu.cn/tzgg.htm",
                       {"item": 'li > a[href*="info/"]', "title": ".text p", "date": [".date span", ".date p"]})
    assert len(items) == 2
    assert items[0].url == "https://jwc.scu.edu.cn/info/1069/10920.htm"
    assert items[0].published_at.date().isoformat() == "2026-11-04"


def test_parse_list_has_selector_skips_nav():
    items = parse_list(SW, "https://sw.scu.edu.cn/xwgg/xytz.htm",
                       {"item": "li:has(span.fr)", "title": "a@title", "link": "a", "date": "span.fr"})
    assert [i.title for i in items] == ["芷云奖助学金评选通知"]
    assert items[0].url == "https://sw.scu.edu.cn/info/1038/15998.htm"


def test_extract_detail_strips_style():
    html = """<html><head><title>x</title></head><body><h1>完整标题</h1>
    <div class="v_news_content"><style>p.MsoNormal{margin:0}</style><p>报名截止时间：11月20日17:00，请本科生按时提交材料。</p></div></body></html>"""
    content, title = extract_detail(html, {"title": "h1", "content": [".v_news_content"]})
    assert "MsoNormal" not in content and "11月20日" in content
    assert title == "完整标题"


def test_parse_feed():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Hello</title><link>https://a.com/1</link><pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate>
    <description>&lt;p&gt;正文 &lt;b&gt;加粗&lt;/b&gt;&lt;/p&gt;</description></item></channel></rss>"""
    items = parse_feed(xml)
    assert items[0].title == "Hello" and "加粗" in items[0].summary and "<b>" not in items[0].summary
    assert items[0].published_at.hour == 16  # 转成北京时间


def test_json_mapping():
    data = {"data": {"list": [{"name": "A", "html_url": "https://x/1", "published_at": "2026-11-01T00:00:00Z", "body": "b"}]}}
    items = map_items(data, {"items_path": "data.list", "fields": {"title": "name", "url": "html_url", "date": "published_at", "summary": "body"}})
    assert items[0].title == "A" and items[0].url == "https://x/1"
