"""Where MindLoom is, who is signed in, and whether the person allowed automations here.

Stored in ``%APPDATA%\\MindLoom\\agent.json``. The access token is encrypted for the
signed-in Windows user with DPAPI before it is written; on other systems (the tests) it is
kept as it is.

The site comes from, in order: ``MINDLOOM_WEB_BASE`` / ``MINDLOOM_API_BASE``, an
``install.json`` in that folder or next to ``MindLoom.exe`` (``{"webBase": ..., "apiBase": ...}``,
written by ``scripts/build.ps1 -WebBase``), the saved config, then the local development defaults.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_WEB_BASE = "http://localhost:5500"
DEFAULT_API_BASE = "http://localhost:8000"


def config_dir() -> Path:
    override = os.environ.get("MINDLOOM_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".mindloom")
    return Path(base) / "MindLoom"


def resolve_api_base(api_base: str, web_base: str) -> str:
    """Absolute API origin. Relative values such as ``/api`` are resolved against the site."""

    api, web = api_base.strip(), web_base.strip().rstrip("/")
    if api.lower().startswith(("http://", "https://")):
        return api.rstrip("/")
    if not api:
        return web
    return web + (api if api.startswith("/") else "/" + api)


def _is_http(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


@dataclass
class Config:
    web_base: str = DEFAULT_WEB_BASE
    api_base: str = DEFAULT_API_BASE
    access_token: str = ""
    org_id: str = ""
    user_id: str = ""
    email: str = ""
    # The person said yes to "let MindLoom operate apps on this PC" (asked on the first task).
    consented: bool = False
    # The one address inside this network the person let the relay call (see relay.py).
    # Tied to the sign-in: signing out forgets it.
    relay_origin: str = ""
    # This PC's relay key (see relay.py). Kept across sign-outs: it names the PC, not the person.
    relay_key: str = ""

    @property
    def signed_in(self) -> bool:
        return bool(self.access_token.strip())

    def signed_out(self) -> "Config":
        return Config(web_base=self.web_base, api_base=self.api_base, consented=self.consented,
                      relay_key=self.relay_key)


# --- DPAPI ------------------------------------------------------------------------------------


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
    if not call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("Windows could not protect the sign-in token.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(token: str) -> str:
    if not token:
        return ""
    if sys.platform != "win32":
        return "plain:" + token
    return "dpapi:" + base64.b64encode(_dpapi(token.encode("utf-8"), True)).decode("ascii")


def unprotect(stored: str) -> str:
    try:
        if stored.startswith("dpapi:") and sys.platform == "win32":
            return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
        if stored.startswith("plain:") and sys.platform != "win32":
            return stored[6:]
    except (OSError, ValueError):
        pass
    return ""  # unreadable (another Windows user, another PC): sign in again


# --- Load and save ------------------------------------------------------------------------------


def _install_override(directory: Path) -> tuple[str, str] | None:
    """The site an installer chose: ``install.json`` in the config folder, or next to MindLoom.exe."""

    places = [directory]
    if getattr(sys, "frozen", False):
        places.append(Path(sys.executable).resolve().parent)
    for place in places:
        try:
            data = json.loads((place / "install.json").read_text(encoding="utf-8-sig"))
            web = str(data.get("webBase") or "").strip().rstrip("/")
            if _is_http(web):
                return web, resolve_api_base(str(data.get("apiBase") or "/api"), web)
        except (OSError, ValueError, AttributeError):
            continue
    return None


def load() -> Config:
    directory = config_dir()
    config = Config()
    try:
        data = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
        config = Config(
            web_base=str(data.get("web_base") or DEFAULT_WEB_BASE).rstrip("/"),
            api_base=str(data.get("api_base") or DEFAULT_API_BASE).rstrip("/"),
            access_token=unprotect(str(data.get("access_token") or "")),
            org_id=str(data.get("org_id") or ""), user_id=str(data.get("user_id") or ""),
            email=str(data.get("email") or ""), consented=data.get("consented") is True,
            relay_origin=str(data.get("relay_origin") or ""),
            relay_key=unprotect(str(data.get("relay_key") or "")),
        )
    except (OSError, ValueError, AttributeError):
        pass

    site = _install_override(directory)
    env_web = os.environ.get("MINDLOOM_WEB_BASE", "").strip().rstrip("/")
    if _is_http(env_web):
        site = (env_web, resolve_api_base(os.environ.get("MINDLOOM_API_BASE", "/api"), env_web))
    if site and (site[0] != config.web_base or site[1] != config.api_base):
        # A token from one MindLoom is not valid on another: switching sites signs out.
        config = Config(web_base=site[0], api_base=site[1], consented=config.consented, relay_key=config.relay_key)
    return config


def save(config: Config) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    data["access_token"] = protect(config.access_token)
    data["relay_key"] = protect(config.relay_key)
    target = directory / "agent.json"
    temporary = directory / "agent.json.tmp"
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def ensure_relay_key(config: Config) -> Config:
    """Give this PC a relay key the first time one is needed, and save it."""

    if not config.relay_key:
        import secrets

        config.relay_key = secrets.token_urlsafe(32)
        save(config)
    return config
