import os

os.environ["INFO_STATION_NO_SCHEDULER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_api_smoke(tmp_env, monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert c.get("/api/items").json()["total"] == 0
        r = c.post("/api/sources", json={"id": "demo", "name": "Demo", "type": "rss", "url": "https://example.com/feed", "category": "tech"})
        assert r.status_code == 200
        assert c.post("/api/sources", json={"id": "Bad ID", "name": "x", "type": "rss"}).status_code == 400
        ids = [s["id"] for s in c.get("/api/sources").json()]
        assert [i for i in ids if not i.startswith("mail-")] == ["demo"]
        assert {"mail-scu", "mail-qq", "mail-gmail", "mail-outlook"} <= set(ids)  # 首次启动补上的邮箱示例（默认停用）
        # 密钥写入后不会明文返回
        s = c.put("/api/settings", json={"llm": {"api_key": "sk-test-1234567890"}}).json()  # gitleaks:allow（测试用假 Key）
        assert s["llm"]["api_key"] == "" and s["llm"]["api_key_masked"].endswith("7890")
        # 空字符串 = 不修改
        s = c.put("/api/settings", json={"llm": {"api_key": "", "model": "deepseek-flash"}}).json()
        assert s["llm"]["api_key_masked"].endswith("7890")
        r = c.post("/api/ingest/items", json={"source_id": "demo", "items": [{"title": "外部推送", "url": "https://x/1"}]})
        assert r.json()["inserted"] == 1
        assert c.delete("/api/sources/demo").status_code == 200
