import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """隔离的数据目录 + 关闭模型（不产生任何外部调用）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    s = config.Settings()
    s.llm.enabled = False
    monkeypatch.setattr(config, "_settings", s)
    return s
