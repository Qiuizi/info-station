"""本机密钥保管：邮箱授权码 / 应用专用密码 / OAuth 刷新令牌。

Windows 上存进「凭据管理器」（keyring 的 WinVaultKeyring，按 Windows 账户加密），
绝不写进 sources.yaml、settings.json 或仓库。条目名形如 ``InfoStation/<name>``，
可在「控制面板 → 凭据管理器 → Windows 凭据」里看到和删除。
"""
from __future__ import annotations

import logging
import sys

SERVICE = "InfoStation"
log = logging.getLogger("vault")
_backend = None


class VaultError(RuntimeError):
    pass


def _kr():
    """取 keyring 后端：Windows 显式用凭据管理器（打包后不依赖 entry point 自动发现）。"""
    global _backend
    if _backend is not None:
        return _backend
    try:
        import keyring
    except ImportError as e:  # pragma: no cover
        raise VaultError("缺少 keyring 组件，无法安全保存密码") from e
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())
    _backend = keyring
    return keyring


def backend_name() -> str:
    try:
        return type(_kr().get_keyring()).__name__
    except Exception as e:  # noqa: BLE001
        return f"不可用（{e}）"


def get(name: str) -> str | None:
    try:
        return _kr().get_password(SERVICE, name)
    except VaultError:
        raise
    except Exception as e:  # noqa: BLE001
        raise VaultError(f"读取系统凭据失败：{e}") from e


def set(name: str, value: str) -> None:  # noqa: A001
    if not value:
        raise VaultError("内容为空")
    try:
        _kr().set_password(SERVICE, name, value)
    except VaultError:
        raise
    except Exception as e:  # noqa: BLE001
        raise VaultError(f"写入系统凭据失败：{e}") from e


def delete(name: str) -> None:
    try:
        _kr().delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001 — 本来就没有
        pass


def has(name: str) -> bool:
    try:
        return bool(get(name))
    except VaultError:
        return False
