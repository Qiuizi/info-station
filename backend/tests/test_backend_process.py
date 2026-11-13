"""打包版后端进程——桌面壳退出时后端不残留；无控制台时日志写文件。"""
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_backend_exits_when_parent_exits():
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child = subprocess.Popen([sys.executable, "-c",
                              "import sys, time; sys.path.insert(0, '.'); import run; "
                              f"run.watch_parent({parent.pid}); time.sleep(60)"], cwd=BACKEND)
    try:
        time.sleep(1.0)
        assert child.poll() is None  # 父进程还在：后端照常运行
        parent.kill()
        parent.wait()
        child.wait(timeout=15)  # 父进程退出后几秒内跟着退出
        assert child.returncode == 0
    finally:
        for p in (parent, child):
            if p.poll() is None:
                p.kill()


def test_no_parent_pid_means_no_watcher():
    sys.path.insert(0, str(BACKEND))
    import run

    assert run.watch_parent(None) is None


def test_frozen_logs_to_file(tmp_path):
    code = (
        "import sys; sys.path.insert(0, '.'); sys.frozen = True\n"
        "from app import config; config.DATA_DIR = __import__('pathlib').Path(sys.argv[1])\n"
        "import run; run._log_to_file_when_frozen(); print('后端日志 OK'); sys.stdout.flush()\n"
    )
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=BACKEND, check=True)
    assert "后端日志 OK" in (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8")
