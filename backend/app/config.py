"""路径、信源配置（YAML）与用户设置（JSON）的读写。

开发模式：项目根目录下的 config/、prompts/、data/。
打包模式（PyInstaller sidecar）：默认配置随包携带，首次启动时复制到
%LOCALAPPDATA%/InfoStation/ 下，之后都读写那里，方便用户修改。
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

FROZEN = getattr(sys, "frozen", False)

if FROZEN:  # PyInstaller
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BUNDLE_ROOT = Path(__file__).resolve().parents[2]  # 项目根目录


def _default_data_dir() -> Path:
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        return Path(base) / "InfoStation"
    return BUNDLE_ROOT / "data"


DATA_DIR = Path(os.environ.get("INFO_STATION_DATA_DIR") or _default_data_dir())
CONFIG_DIR = Path(os.environ.get("INFO_STATION_CONFIG_DIR") or (DATA_DIR / "config" if FROZEN else BUNDLE_ROOT / "config"))
PROMPTS_DIR = Path(os.environ.get("INFO_STATION_PROMPTS_DIR") or (DATA_DIR / "prompts" if FROZEN else BUNDLE_ROOT / "prompts"))
DB_PATH = Path(os.environ.get("INFO_STATION_DB") or DATA_DIR / "info_station.db")
SETTINGS_PATH = DATA_DIR / "settings.json"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if FROZEN:
        # 安装版：第一次运行整份复制；之后只补新版本新增的文件（如新分类的提示词），已有文件一律不覆盖
        for name, target in (("config", CONFIG_DIR), ("prompts", PROMPTS_DIR)):
            src = BUNDLE_ROOT / name
            if not src.exists():
                continue
            if not target.exists():
                shutil.copytree(src, target)
                continue
            for f in src.rglob("*"):
                dst = target / f.relative_to(src)
                if f.is_file() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(f, dst)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- settings
class LLMSettings(BaseModel):
    enabled: bool = True
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key: str = ""  # 留空则读环境变量 DEEPSEEK_API_KEY
    dual_score: bool = False  # 借鉴 AIHOT：同一标准独立打两次分，压住单次抖动
    disable_thinking: bool = True  # 批量筛选关闭思考模式，更快更省
    temperature: float = 0.3
    max_items_per_run: int = 40
    concurrency: int = 3
    timeout_seconds: int = 60
    send_content: bool = True  # 是否把正文（而非仅标题）发送给模型
    daily_call_budget: int = 600  # 熔断：每天最多调用次数
    max_age_days: int = 30  # 发布超过 N 天且没有未到截止的旧文（含首次导入的存量）不送模型，只保留规则分


class Thresholds(BaseModel):
    def featured_for(self, category: str | None) -> int:
        return int(self.by_category.get(category or "", self.featured))


    featured: int = 60  # ≥ 此分进入“今日精选”（未在 by_category 中单独设置的分类用它）
    # 分类门槛：学校/竞赛通知对本人是一手且需要行动的信息，门槛低于泛技术/新闻
    by_category: dict[str, int] = Field(default_factory=lambda: {"school": 55, "competition": 55, "tech": 62, "news": 65, "email": 55})
    notify: int = 78  # ≥ 此分且非存量，弹 Windows 通知
    browse_floor: int = 20  # “全部”视图默认隐藏低于此分的条目（搜索不受影响）
    t2_offset: int = 10  # T2 信源门槛上浮
    stale_hours: int = 48  # 发现时已发布超过 N 小时的旧文不推送


class Profile(BaseModel):
    name: str = "同学"
    school: str = "四川大学"
    college: str = "软件学院"
    major: str = "软件工程"
    grade: str = "2025级"  # 只需写入学年份；当前是大几按日期自动推算（9 月开学换年级）
    interests: list[str] = Field(default_factory=lambda: [
        "CSP 认证", "算法竞赛", "大创", "实验室 / 科研", "大学英语六级", "奖学金", "AI 与大模型", "开源项目",
    ])
    dislikes: list[str] = Field(default_factory=lambda: ["招标采购", "党建例行", "领导接待日", "研究生专属"])
    notes: str = "低年级本科生，希望尽早参与竞赛和科研；关注报名截止时间。"


class FetchSettings(BaseModel):
    default_interval_minutes: int = 30
    timeout_seconds: int = 25
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
    browser_channel: str = "auto"  # auto / msedge / chrome / chromium
    browser_headless: bool = True
    max_detail_fetch: int = 6
    rsshub_base: str = "http://127.0.0.1:1200"
    github_token: str = ""  # 留空则读环境变量 GITHUB_TOKEN
    # 全局代理（默认本机 Clash Verge 的 7897 端口）：只对勾选了 use_proxy 的信源生效（X 信源默认勾选），
    # httpx 与 Playwright 都会用它。清空后改为读取 Windows 系统代理（Internet 设置里的手动代理）。
    proxy_url: str = "http://127.0.0.1:7897"
    proxy_use_system: bool = True

    @field_validator("proxy_url")
    @classmethod
    def _proxy(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.match(r"^(https?|socks5h?)://[^\s/]+(:\d+)?/?$", v):
            raise ValueError("代理地址格式应为 http://127.0.0.1:7897（支持 http / https / socks5）")
        return v.rstrip("/")


class ScoringSettings(BaseModel):
    """模型打分改为每天一次批量，放在 DeepSeek 谷时（见 config/deepseek_offpeak.yaml）。"""
    time: str = "07:30"  # 北京时间；07:30 在任何日子都是谷时，且早于早上使用
    catch_up: bool = True  # 定时点没开机：开机后若在谷时就补跑，否则顺延到下一个谷时
    skip_catchup_within_minutes: int = 90  # 距下次定时不足 N 分钟就不补跑，直接等定时
    stop_at_peak: bool = True  # 批量跑到一半进入高峰时暂停，余下的到谷时再继续（手动强制打分除外）

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", v or "")
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("打分时间格式应为 HH:MM")
        return f"{int(m.group(1)):02d}:{m.group(2)}"


def _hhmm_value(v: str, what: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", v or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{what}格式应为 HH:MM")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


class XSettings(BaseModel):
    """X（Twitter）信源：用你自己登录的浏览器像人一样打开搜索页 / 主页，低频、少量。"""
    time: str = "07:00"  # 每天一次，北京时间；默认早于 07:30 的谷时打分，抓到的推文当天就能打分
    catch_up: bool = True  # 定时点没开机：开机后当天补抓一次
    min_delay_seconds: int = Field(8, ge=2, le=300)  # 每个关键词 / 账号之间随机等待
    max_delay_seconds: int = Field(40, ge=2, le=600)
    per_target_max: int = Field(30, ge=1, le=100)  # 每个关键词 / 账号最多取多少条
    max_scrolls: int = Field(6, ge=0, le=30)  # 每页最多向下滚动几次
    run_max: int = Field(150, ge=1, le=1000)  # 单次运行总条数上限
    daily_max: int = Field(300, ge=1, le=3000)  # 每天总条数上限（多个 X 信源合计）
    max_age_days: int = Field(3, ge=1, le=30)  # 只要最近 N 天的推文（置顶旧帖、翻出来的旧帖丢掉）
    headless: bool = True  # 被要求验证时可改成 false，用可见窗口抓
    show_avatars: bool = False  # 信息流显示头像（头像图床在大陆通常也要代理）

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        return _hhmm_value(v, "X 抓取时间")

    @model_validator(mode="after")
    def _delays(self) -> XSettings:
        if self.max_delay_seconds < self.min_delay_seconds:
            raise ValueError("X 抓取的最大间隔不能小于最小间隔")
        return self


class NotifySettings(BaseModel):
    enabled: bool = True
    quiet_start: str = "23:30"
    quiet_end: str = "07:30"


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    profile: Profile = Field(default_factory=Profile)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    x: XSettings = Field(default_factory=XSettings)
    scheduler_enabled: bool = True


_lock = threading.RLock()
_settings: Settings | None = None


def load_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            if SETTINGS_PATH.exists():
                try:
                    _settings = Settings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
                except Exception:
                    _settings = Settings()
            else:
                _settings = Settings()
        return _settings


def save_settings(s: Settings) -> None:
    global _settings
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(s.model_dump_json(indent=2), encoding="utf-8")
        _settings = s


def deepseek_key(s: Settings | None = None) -> str:
    s = s or load_settings()
    return (s.llm.api_key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()


def github_token(s: Settings | None = None) -> str:
    s = s or load_settings()
    return (s.fetch.github_token or os.environ.get("GITHUB_TOKEN", "")).strip()


def mask(secret: str) -> str:
    if not secret:
        return ""
    return secret[:3] + "…" + secret[-4:] if len(secret) > 10 else "••••"


# ---------------------------------------------------------------- sources
CATEGORIES = {
    "school": "学校通知",
    "competition": "竞赛科研",
    "tech": "技术动态",
    "news": "新闻话题",
    "email": "邮件",
}
SOURCE_TYPES = ["rss", "html_list", "json", "github", "playwright", "feishu", "x", "email"]


def sources_path() -> Path:
    return CONFIG_DIR / "sources.yaml"


def load_sources() -> list[dict[str, Any]]:
    p = sources_path()
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for s in data.get("sources", []) or []:
        s = dict(s)
        s.setdefault("enabled", True)
        s.setdefault("tier", "T2")
        s.setdefault("category", "news")
        out.append(s)
    return out


def save_sources(sources: list[dict[str, Any]]) -> None:
    with _lock:
        p = sources_path()
        header = "# 信源配置：可在桌面端「信源管理」里编辑，也可以直接改这个文件（保存后点“重新加载”）。\n"
        body = yaml.safe_dump({"sources": sources}, allow_unicode=True, sort_keys=False, width=120)
        p.write_text(header + body, encoding="utf-8")


def ensure_example_sources(examples: list[dict[str, Any]], marker: str) -> list[str]:
    """把示例信源（默认停用）一次性补进 sources.yaml：只补缺的，补过一次就不再补（你删掉的不会回来）。

    追加在文件末尾、保留原有注释；追加后解析不出来就退回整份重写。返回补进去的 id。
    """
    from . import db

    if db.get_state(marker):
        return []
    with _lock:
        have = {s.get("id") for s in load_sources()}
        missing = [e for e in examples if e["id"] not in have]
        p = sources_path()
        if missing and p.exists():
            text = p.read_text(encoding="utf-8")
            block = yaml.safe_dump(missing, allow_unicode=True, sort_keys=False, width=120)
            m = re.findall(r"(?m)^( *)- id:", text)
            ind = m[-1] if m else "  "
            block = "".join(ind + line if line.strip() else line for line in block.splitlines(True))
            new = text.rstrip("\n") + f"\n{ind}# ---- 示例信源（默认停用，填好地址和授权码后再打开）\n" + block
            p.write_text(new, encoding="utf-8")
            try:
                ok = {e["id"] for e in missing} <= {s.get("id") for s in load_sources()}
            except yaml.YAMLError:
                ok = False
            if not ok:
                p.write_text(text, encoding="utf-8")
                save_sources(load_sources() + missing)
        elif missing:
            save_sources(missing)
    db.set_state(marker, True)
    return [e["id"] for e in missing]


def get_source(source_id: str) -> dict[str, Any] | None:
    for s in load_sources():
        if s.get("id") == source_id:
            return copy.deepcopy(s)
    return None


def load_rules() -> dict[str, Any]:
    p = CONFIG_DIR / "rules.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


