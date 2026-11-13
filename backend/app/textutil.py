"""文本、URL、日期与截止日期抽取等纯函数（便于单元测试）。"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as du_parser

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from", "ref"}


def now() -> datetime:
    return datetime.now(TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def normalize_deadline(raw: str | None, ref: datetime | None = None) -> datetime | None:
    """模型与规则共用的截止时间归一化：返回北京时间。

    - “X日24点 / 24:00” → X 日 23:59；只有日期没有时刻 → 当天 23:59
    - ISO 带 Z / 时区偏移的换算到北京时间；不带的按北京时间
    - “11月12日”这类没有年份的按 ref（默认今天）所在年份推算
    """
    if not raw:
        return None
    t = norm_24h(str(raw).strip())
    if not t or t.lower() in ("null", "none", "无", "未知"):
        return None
    has_time = bool(re.search(r"\d{1,2}\s*[:：时点]\s*\d{0,2}", t)) or bool(re.search(r"T\d{2}", t))
    dt = _from_iso_any(t)
    if dt is None:
        ds = _dates_in(t, ref or now())
        dt = ds[0][0] if ds else None
        if dt is not None:
            return dt  # _dates_in 已处理“没写时刻 → 23:59”
    if dt is None:
        dt = parse_date_loose(t)
    if dt is None:
        return None
    if not has_time and dt.hour == 0 and dt.minute == 0:
        dt = dt.replace(hour=23, minute=59)
    return dt.astimezone(TZ)


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING))
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, netloc, path, query, ""))


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)
_GB_ALIASES = {"gb2312", "gbk", "gb_2312-80", "x-gbk", "cp936", "gb18030"}


def decode_bytes(data: bytes, declared: str | None = None) -> str:
    """网页字节解码：HTTP 头里的 charset → 页面 <meta charset> → 严格 UTF-8 → GB18030。

    GB2312/GBK 一律按超集 GB18030 解。不再“先猜 UTF-8”：短的中文 GBK 字节有时恰好也是合法 UTF-8
    （例如“学校通知”的 GBK 字节按 UTF-8 解出来是“ѧУ֪ͨ”），先猜会静默出错。
    """
    enc = (declared or "").strip().lower()
    if not enc or enc in ("iso-8859-1", "ascii", "us-ascii", "latin-1"):
        m = _META_CHARSET.search(data[:4096])
        enc = m.group(1).decode("ascii", "ignore").lower() if m else ""
    if enc in _GB_ALIASES:
        enc = "gb18030"
    if enc:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace")


def repair_gbk_mojibake(text: str) -> str:
    """把“GBK 字节被当成 UTF-8 解码”的乱码还原（ѧУ֪ͨ → 学校通知）；不像这种乱码就原样返回。"""
    if not text or not re.search(r"[\u0370-\u07ff]", text) or re.search(r"[\u4e00-\u9fff]", text):
        return text
    try:
        fixed = text.encode("utf-8").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed if re.search(r"[\u4e00-\u9fff]", fixed) else text


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def clean_text(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    text = re.sub(r"[\u00a0\u3000\t\r ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def title_key(title: str) -> str:
    """去掉空白、标点与常见前后缀，用于跨源判重。"""
    t = re.sub(r"[【\[（(].{0,12}?[】\]）)]", "", title)
    t = _PUNCT.sub("", t.lower())
    return t


def bigrams(text: str) -> set[str]:
    t = title_key(text)
    return {t[i : i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def similarity(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


# ---------------------------------------------------------------- 日期
_YMD = re.compile(r"(20\d{2})\s*[-./年]\s*(\d{1,2})\s*[-./月]\s*(\d{1,2})")
_Y_THEN_MD = re.compile(r"(20\d{2})[^\d:]{1,6}?(\d{1,2})[^\d:]{1,3}?(\d{1,2})(?![\d:])")
_MD_THEN_Y = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.月]\s*(\d{1,2})\D{0,4}?(20\d{2})")


_ISO = re.compile(r"^\s*20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?\s*$", re.I)
_H24 = re.compile(r"(?<!\d)24\s*(?:[:：]\s*00(?:[:：]00)?|[点时](?:\s*0{1,2}\s*分?|整)?)")


def norm_24h(text: str) -> str:
    """“24点 / 24时 / 24:00” 是当天结束，统一改写为 23:59（避免被当成次日或被丢弃）。"""
    return _H24.sub("23:59", text)


def _from_iso_any(t: str) -> datetime | None:
    """严格 ISO 8601（含 Z / ±HH:MM）：带时区的换算到北京时间，不带的按北京时间。"""
    if not _ISO.match(t):
        return None
    try:
        dt = datetime.fromisoformat(t.strip().replace("z", "Z").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)


def parse_date_loose(text: str | None, default_tz=TZ) -> datetime | None:
    """解析各种列表页日期：2026-11-10、2026.11 05、2026 11/05、11/05 2026、RFC822、ISO …

    带时区的（ISO 的 Z/±08:00、RFC822 的 GMT/+0000）一律换算成北京时间；不带时区的视为北京时间。
    """
    if not text:
        return None
    t = norm_24h(clean_text(text).replace("\n", " "))
    iso = _from_iso_any(t)
    if iso:
        return iso
    for rx, order in ((_YMD, (1, 2, 3)), (_Y_THEN_MD, (1, 2, 3)), (_MD_THEN_Y, (3, 1, 2))):
        m = rx.search(t)
        if m:
            dt = _mk(int(m[order[0]]), int(m[order[1]]), int(m[order[2]]), t, default_tz)
            if dt:
                return dt
    if not re.search(r"20\d{2}", t):
        return None  # 没有年份时不交给 dateutil 猜（它会把“24点”里的 24 当成年份）
    try:
        dt = du_parser.parse(t, fuzzy=True)
        if dt.year < 2000:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.astimezone(TZ)
    except (ValueError, OverflowError):
        return None


def _mk(y: int, mo: int, d: int, text: str, tz) -> datetime | None:
    try:
        hm = re.search(r"(\d{1,2}):(\d{2})", text)
        h, mi = (int(hm[1]), int(hm[2])) if hm else (0, 0)
        return datetime(y, mo, d, h, mi, tzinfo=tz)
    except ValueError:
        return None


def to_iso(dt: datetime | None) -> str | None:
    return dt.astimezone(TZ).isoformat(timespec="seconds") if dt else None


def from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
    except ValueError:
        return None


# ---------------------------------------------------------------- 截止日期
_DL_KEYWORDS = re.compile(
    r"截止|截至|报名时间|申报时间|报名日期|提交时间|申请时间|报名|申报|提交|之前|以前|deadline|due",
    re.IGNORECASE,
)
_CN_DATE = re.compile(
    r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"
    r"(?:\s*[（(]?[周星期礼拜][一二三四五六日天][)）]?)?"
    r"(?:\s*(?:(上午|下午|晚上|中午)?\s*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?\s*分?))?"
)
_NUM_DATE = re.compile(r"(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?:\s*(\d{1,2}):(\d{2}))?")


def _dates_in(sentence: str, ref: datetime) -> list[tuple[datetime, int]]:
    sentence = norm_24h(sentence)
    found: list[tuple[datetime, int]] = []
    for m in _CN_DATE.finditer(sentence):
        y = int(m[1]) if m[1] else ref.year
        hour = int(m[5]) if m[5] else 23
        minute = int(m[6]) if m[6] else (59 if not m[5] else 0)
        if m[4] in ("下午", "晚上") and hour < 12:
            hour += 12
        try:
            dt = datetime(y, int(m[2]), int(m[3]), min(hour, 23), min(minute, 59), tzinfo=TZ)
        except ValueError:
            continue
        if not m[1] and dt < ref - timedelta(days=60):
            dt = dt.replace(year=dt.year + 1)
        found.append((dt, m.start()))
    for m in _NUM_DATE.finditer(sentence):
        try:
            hour = int(m[4]) if m[4] else 23
            minute = int(m[5]) if m[5] else 59
            dt = datetime(int(m[1]), int(m[2]), int(m[3]), hour, minute, tzinfo=TZ)
        except ValueError:
            continue
        found.append((dt, m.start()))
    found.sort(key=lambda x: x[1])
    return found


def extract_deadline(text: str, ref: datetime | None = None) -> tuple[datetime | None, str]:
    """规则抽取截止日期：找含“截止/报名/申报/之前”等关键词的句子，取其中最后一个日期（区间取结束）。

    返回 (截止时间, 原句片段)。多个候选时取发布之后最早的那个。
    """
    if not text:
        return None, ""
    ref = ref or now()
    sentences = []
    for sent in re.split(r"[。；;！!\n]", text):
        # 括号里的补充说明（如“学院审核截止 11 月 13 日中午 12 点”）单独成句，不和正文的截止时间混在一起取“最后一个”
        inner = re.findall(r"[（(]([^（()）]{2,80})[)）]", sent)
        sentences.append(re.sub(r"[（(][^（()）]{2,80}[)）]", " ", sent))
        sentences.extend(inner)
    candidates: list[tuple[datetime, str]] = []
    for s in sentences:
        if not _DL_KEYWORDS.search(s):
            continue
        ds = _dates_in(s, ref)
        if not ds:
            continue
        dt = ds[-1][0]
        if dt >= ref - timedelta(days=1):
            candidates.append((dt, s.strip()[:120]))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0]
