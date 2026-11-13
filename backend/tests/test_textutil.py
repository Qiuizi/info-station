from datetime import datetime

from app.textutil import TZ, extract_deadline, normalize_url, parse_date_loose, similarity


def test_parse_date_variants():
    assert parse_date_loose("2026-11-10").date().isoformat() == "2026-11-10"
    assert parse_date_loose("2026.11 05").date().isoformat() == "2026-11-05"  # 川大官网 p=YYYY.MM span=DD
    assert parse_date_loose("2026 11/04").date().isoformat() == "2026-11-04"  # 教务处 span=YYYY p=MM/DD
    assert parse_date_loose("11/04 2026").date().isoformat() == "2026-11-04"
    assert parse_date_loose("2026年3月7日 14:30").hour == 14
    assert parse_date_loose("Tue, 10 Nov 2026 08:00:00 GMT").astimezone(TZ).hour == 16
    assert parse_date_loose("") is None
    assert parse_date_loose("没有日期") is None


def test_extract_deadline_range_and_year_inference():
    ref = datetime(2026, 11, 5, tzinfo=TZ)
    text = "一、报名时间：11月6日至11月20日17:00。二、项目时间：2027年1月17日至1月30日。"
    dt, note = extract_deadline(text, ref)
    assert (dt.month, dt.day, dt.hour) == (11, 20, 17)
    assert "报名" in note


def test_extract_deadline_cross_year_and_none():
    ref = datetime(2026, 12, 20, tzinfo=TZ)
    dt, _ = extract_deadline("请于1月5日前提交申请材料", ref)
    assert (dt.year, dt.month, dt.day) == (2027, 1, 5)
    assert extract_deadline("学院召开教学工作会议", ref) == (None, "")


def test_extract_deadline_numeric():
    ref = datetime(2026, 11, 1, tzinfo=TZ)
    dt, _ = extract_deadline("申报截止时间：2026-11-15 18:00", ref)
    assert (dt.day, dt.hour) == (15, 18)


def test_normalize_url_and_similarity():
    assert normalize_url("HTTP://Jwc.SCU.edu.cn/info/1.htm?utm_source=x&b=2&a=1#top") == "https://jwc.scu.edu.cn/info/1.htm?a=1&b=2"
    assert similarity("关于2026年芷云奖助学金评选的通知", "【学院】关于2026年芷云奖助学金评选通知") > 0.6
    assert similarity("期中考试安排", "GitHub Trending") < 0.1
