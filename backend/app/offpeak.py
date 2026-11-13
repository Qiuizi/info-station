"""DeepSeek 峰谷时段判断（北京时间）。

官方规则（https://api-docs.deepseek.com/zh-cn/quick_start/pricing）：周一至周五（不含中国法定节假日）
9:00-12:00、14:00-18:00 为高峰；其余时段（含周末、法定节假日全天）为空闲时段，价格为高峰的一半。
规则与节假日表放在 config/deepseek_offpeak.yaml，缺失时用下面的内置默认值。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

import yaml

from . import config
from .textutil import TZ

PRICING_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"

_DEFAULT: dict[str, Any] = {
    "peak_weekdays": [0, 1, 2, 3, 4],
    "peak_windows": [["09:00", "12:00"], ["14:00", "18:00"]],
    "holidays": [
        "2026-01-01..2026-01-03", "2026-02-15..2026-02-23", "2026-04-04..2026-04-06", "2026-05-01..2026-05-05",
        "2026-06-19..2026-06-21", "2026-09-25..2026-09-27", "2026-10-01..2026-10-07",
        "2027-01-01..2027-01-03", "2027-02-05..2027-02-13", "2027-04-03..2027-04-05", "2027-05-01..2027-05-05",
        "2027-06-09", "2027-09-15", "2027-10-01..2027-10-07",
    ],
}


@dataclass(frozen=True)
class PeakRules:
    weekdays: frozenset[int]
    windows: tuple[tuple[time, time], ...]
    holidays: frozenset[date]

    def windows_text(self) -> list[list[str]]:
        return [[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in self.windows]


def _hm(s: str) -> time:
    h, m = str(s).strip().split(":")
    return time(int(h), int(m))


def _dates(spec: Any) -> list[date]:
    s = str(spec).strip()
    if ".." in s:
        a, b = (date.fromisoformat(x.strip()) for x in s.split("..", 1))
        return [a + timedelta(days=i) for i in range((b - a).days + 1)]
    return [date.fromisoformat(s)]


def parse_rules(raw: dict[str, Any] | None) -> PeakRules:
    r = {**_DEFAULT, **(raw or {})}
    hol: set[date] = set()
    for spec in r.get("holidays") or []:
        hol.update(_dates(spec))
    wins = tuple(sorted((_hm(a), _hm(b)) for a, b in r.get("peak_windows") or []))
    return PeakRules(frozenset(int(x) for x in r.get("peak_weekdays") or []), wins, frozenset(hol))


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> PeakRules:  # noqa: ARG001 — mtime 只用于缓存失效
    with open(path, encoding="utf-8") as f:
        return parse_rules(yaml.safe_load(f) or {})


def rules() -> PeakRules:
    p = config.CONFIG_DIR / "deepseek_offpeak.yaml"
    try:
        return _load(str(p), p.stat().st_mtime)
    except (OSError, ValueError, yaml.YAMLError):
        return parse_rules(None)


def _local(t: datetime) -> datetime:
    return t.astimezone(TZ) if t.tzinfo else t.replace(tzinfo=TZ)


def is_peak_day(d: date, r: PeakRules | None = None) -> bool:
    r = r or rules()
    return d.weekday() in r.weekdays and d not in r.holidays


def is_peak(t: datetime, r: PeakRules | None = None) -> bool:
    r = r or rules()
    t = _local(t)
    if not is_peak_day(t.date(), r):
        return False
    tt = t.time()
    return any(a <= tt < b for a, b in r.windows)


def time_is_peak_on_workdays(hhmm: str, r: PeakRules | None = None) -> bool:
    """某个“每天的时刻”在工作日是否落在高峰（设置页提示用）。"""
    r = r or rules()
    tt = _hm(hhmm)
    return any(a <= tt < b for a, b in r.windows)


def next_offpeak(t: datetime, r: PeakRules | None = None) -> datetime:
    """t 若已是谷时返回 t；否则返回当前高峰窗口结束的时刻（连续窗口会继续往后推）。"""
    r = r or rules()
    t = _local(t)
    for _ in range(16):
        if not is_peak(t, r):
            return t
        tt = t.time()
        end = next(b for a, b in r.windows if a <= tt < b)
        t = datetime.combine(t.date(), end, tzinfo=TZ)
    return t


def next_peak_start(t: datetime, r: PeakRules | None = None) -> datetime | None:
    """t 之后（含）最近一次进入高峰的时刻；14 天内没有则 None。"""
    r = r or rules()
    t = _local(t)
    for i in range(15):
        d = t.date() + timedelta(days=i)
        if not is_peak_day(d, r):
            continue
        for a, _b in r.windows:
            start = datetime.combine(d, a, tzinfo=TZ)
            if start >= t:
                return start
    return None
