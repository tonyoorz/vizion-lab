"""Vizion Lab 看板登录 + 鉴权路由。

通过 BmwLogin 的 session_keeper 服务做 BMW SSO 登录，拿到每个用户各自的
Octane session cookie，并保活。看板自身签发一个 httpOnly session token
（内含 username），下游路由据此构建按用户的 Octane client。

密码持久缓存（Fernet 加密、TTL 7d、落盘）：cookie 过期时 keeper 需要密码才能
重新登录，所以登录成功后把密码加密缓存在内存 + 磁盘文件里。进程重启后从磁盘
reload，不再强制用户重登。磁盘文件是密文（用 VIZION_SESSION_SECRET 派生的
Fernet key 加密），但仍属凭据存储，不可提交（已被 backend/cache/ gitignore 覆盖）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.analytics.config import get_password_cache_file_path

log = logging.getLogger("vizion.auth")

router = APIRouter()

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

SESSION_COOKIE_NAME = "vizion_session"
SESSION_MAX_AGE = 8 * 3600  # 看板 session token 本身 8h

# 密码缓存 TTL：Octane SSO cookie 会自然过期，session_keeper 保活失败时会删掉
# session，此时需要密码才能重新登录。把 TTL 设得足够长（7 天），让密码在大多数
# 场景下都可用；过期后用户需要重新登录一次。
_PASSWORD_CACHE_TTL = 7 * 24 * 3600  # 7d
_fernet: Optional[Fernet] = None
_serializer: Optional[URLSafeTimedSerializer] = None

# username → (加密密码, 过期时间戳)。进程内缓存，启动时从磁盘 reload。
_password_cache: dict[str, tuple[str, float]] = {}
_password_cache_lock = threading.Lock()


def _get_secret() -> bytes:
    secret = os.environ.get("VIZION_SESSION_SECRET", "").strip()
    if not secret:
        # 开发环境兜底：用固定的开发 key（生产必须设置 env）
        secret = "vizion-dev-secret-change-in-production-32+bytes!!"
    return secret.encode() if isinstance(secret, str) else secret


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        # Fernet 需要 32 字节 base64 key；从 secret 派生
        import hashlib
        import base64
        digest = hashlib.sha256(_get_secret()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def _get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(_get_secret(), salt="vizion-session")
    return _serializer


def _get_session_keeper_url() -> str:
    return os.environ.get("SESSION_KEEPER_URL", "http://localhost:8090").rstrip("/")


def _get_octane_tool_key() -> str:
    return os.environ.get("VIZION_OCTANE_KEEPER_TOOL_KEY", "octane")


# --------------------------------------------------------------------------- #
# 密码缓存（内存 + 加密文件持久化）
# --------------------------------------------------------------------------- #

def _persist_password_cache() -> None:
    """把内存里的密码缓存落盘（Fernet 加密）。"""
    cache_file = get_password_cache_file_path()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        # 只持久化未过期的条目
        serializable = {
            user: {"enc": enc, "exp": exp}
            for user, (enc, exp) in _password_cache.items()
            if exp > now
        }
        cache_file.write_text(json.dumps(serializable), encoding="utf-8")
    except Exception:
        log.warning("Failed to persist password cache to %s", cache_file, exc_info=True)


def _load_password_cache() -> None:
    """进程启动时从磁盘 reload 密码缓存。"""
    cache_file = get_password_cache_file_path()
    if not cache_file.is_file():
        return
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        now = time.time()
        loaded = 0
        for user, entry in data.items():
            exp = float(entry.get("exp", 0))
            if exp > now:
                _password_cache[user] = (entry["enc"], exp)
                loaded += 1
        if loaded:
            log.info("Loaded %d cached password(s) from %s", loaded, cache_file)
    except Exception:
        log.warning("Failed to load password cache from %s", cache_file, exc_info=True)


# 进程启动时 reload
_load_password_cache()


def _cache_password(username: str, password: str) -> None:
    encrypted = _get_fernet().encrypt(password.encode()).decode()
    with _password_cache_lock:
        _password_cache[username] = (encrypted, time.time() + _PASSWORD_CACHE_TTL)
        # 清理已过期条目
        now = time.time()
        expired = [u for u, (_, exp) in _password_cache.items() if exp < now]
        for u in expired:
            del _password_cache[u]
        _persist_password_cache()


def _get_cached_password(username: str) -> Optional[str]:
    with _password_cache_lock:
        entry = _password_cache.get(username)
        if entry is None:
            return None
        encrypted, expires = entry
        if time.time() > expires:
            _password_cache.pop(username, None)
            _persist_password_cache()
            return None
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except Exception:
        with _password_cache_lock:
            _password_cache.pop(username, None)
            _persist_password_cache()
        return None


def _clear_cached_password(username: str) -> None:
    with _password_cache_lock:
        _password_cache.pop(username, None)
        _persist_password_cache()


# --------------------------------------------------------------------------- #
# Session token
# --------------------------------------------------------------------------- #

def _issue_session_token(username: str) -> str:
    return _get_serializer().dumps({"username": username})


def _resolve_session_token(token: str) -> Optional[str]:
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data.get("username")
    except (BadSignature, SignatureExpired):
        return None


def get_current_username(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return _resolve_session_token(token)


def require_current_username(request: Request) -> str:
    """FastAPI 依赖：要求已登录，返回 username。"""
    username = get_current_username(request)
    if username is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return username


# --------------------------------------------------------------------------- #
# 调用 session_keeper
# --------------------------------------------------------------------------- #

def _keeper_get_or_create_session(username: str, password: str) -> Optional[object]:
    """向 session_keeper 请求 octane session，返回 requests.Session 或 None。

    复用 BmwLogin 的 session_keeper.client.get_or_create_session。
    """
    try:
        from session_keeper.client import get_or_create_session
    except ImportError:
        log.warning("session_keeper 包未安装，无法做多用户 SSO 登录")
        return None

    try:
        return get_or_create_session(
            tool_key=_get_octane_tool_key(),
            username=username,
            password=password,
        )
    except Exception:
        log.warning("session_keeper 登录失败 for %s", username, exc_info=True)
        return None


def get_user_octane_session(username: str) -> Optional[object]:
    """用缓存的密码从 keeper 拿当前用户的 Octane requests.Session。

    返回 None 表示需要重新登录（密码缓存过期或 keeper 不可用）。
    """
    password = _get_cached_password(username)
    if password is None:
        return None
    return _keeper_get_or_create_session(username, password)


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

class LoginRequest(BaseModel):
    username: str
    password: str
    strong_auth: bool = False
    strong_auth_type: str = "mobile"


@router.post("/api/auth/login")
async def login(body: LoginRequest) -> JSONResponse:
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="username and password required")

    session = _keeper_get_or_create_session(username, body.password)
    if session is None:
        raise HTTPException(
            status_code=401,
            detail="SSO 登录失败：session keeper 不可用或凭证无效",
        )

    _cache_password(username, body.password)
    token = _issue_session_token(username)
    resp = JSONResponse(
        status_code=200,
        content={"username": username, "authenticated": True},
    )
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 本地 http 开发；生产部署到 https 时改 True
        max_age=SESSION_MAX_AGE,
        path="/",
    )
    return resp


@router.get("/api/auth/me")
async def me(request: Request) -> JSONResponse:
    username = get_current_username(request)
    if username is None:
        return JSONResponse(status_code=200, content={"authenticated": False})
    return JSONResponse(
        status_code=200,
        content={"authenticated": True, "username": username},
    )


@router.post("/api/auth/logout")
async def logout(request: Request) -> JSONResponse:
    username = get_current_username(request)
    if username:
        _clear_cached_password(username)
    resp = JSONResponse(status_code=200, content={"detail": "logged out"})
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp
