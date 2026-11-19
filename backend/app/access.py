"""Access-key and origin checks for the local knowledge-base demo."""
import ipaddress
import os
from pathlib import Path
import secrets
import tempfile
from threading import RLock

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings, normalize_host, normalize_origin


TOKEN_LOCK = RLock()
PUBLIC_PATHS = {"/", "/demo", "/fusion", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
API_METHODS = {"GET", "POST", "DELETE", "OPTIONS"}
API_HEADERS = {"authorization", "content-type"}


def get_access_token() -> str:
    """Reuse the configured key or atomically install a generated persistent key."""
    settings = get_settings()
    if settings.access_token:
        return settings.access_token
    path = Path(settings.index_dir).parent / "access_token"
    with TOKEN_LOCK:
        if path.is_symlink():
            raise ValueError("访问密钥文件位置无效")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".access-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(secrets.token_urlsafe(32).encode("ascii"))
                    stream.flush()
                    os.fsync(stream.fileno())
                # A hard link installs the completed file without replacing a winner.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        token = path.read_text(encoding="ascii").strip()
        if len(token) < 32 or any(not 33 <= ord(character) <= 126 for character in token):
            raise ValueError("访问密钥文件无效，请检查服务端配置")
        return token


def _loopback_client(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except (ValueError, AttributeError):
        return False


class AccessControlMiddleware:
    """Enforce authorization on the server and emit explicit CORS responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        settings = get_settings()
        origin = request.headers.get("origin")
        try:
            hosts = request.headers.getlist("host")
            if len(hosts) != 1 or normalize_host(hosts[0]) not in settings.allowed_hosts:
                raise ValueError("请求主机未获允许")
            same_origin = normalize_origin(f"{scope['scheme']}://{hosts[0]}")
            if len(request.headers.getlist("origin")) > 1:
                raise ValueError("请求来源无效")
            if origin is not None and origin != "null":
                if normalize_origin(origin) not in [same_origin, *settings.allowed_origins]:
                    raise ValueError("请求来源未获允许")
        except (ValueError, TypeError):
            await JSONResponse({"detail": "请求主机或来源未获允许。"}, status_code=403)(scope, receive, send)
            return

        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}
        if request.url.path == "/access/token":
            browser_same_origin = request.headers.get("sec-fetch-site") == "same-origin"
            if request.method != "GET" or origin == "null" or (origin and normalize_origin(origin) != same_origin) or not browser_same_origin or not _loopback_client(request):
                await JSONResponse({"detail": "请在本机浏览器打开知识库网页查看访问密钥。"}, status_code=403)(scope, receive, send)
                return
        elif request.url.path not in PUBLIC_PATHS:
            if request.method == "OPTIONS":
                method = request.headers.get("access-control-request-method", "").upper()
                headers = {value.strip().lower() for value in request.headers.get("access-control-request-headers", "").split(",") if value.strip()}
                if not origin or method not in API_METHODS or not headers <= API_HEADERS:
                    await JSONResponse({"detail": "预检请求未获允许。"}, status_code=403, headers=cors)(scope, receive, send)
                    return
                cors.update({
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Max-Age": "600",
                })
                await Response(status_code=204, headers=cors)(scope, receive, send)
                return
            authorization = request.headers.get("authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if scheme.lower() != "bearer" or not supplied or len(request.headers.getlist("authorization")) != 1 or not secrets.compare_digest(supplied.encode("utf-8"), get_access_token().encode("utf-8")):
                await JSONResponse(
                    {"detail": "访问密钥缺失或无效，请在连接设置中更新。"},
                    status_code=401, headers={**cors, "WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return

        async def send_response(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                for name, value in cors.items():
                    message["headers"].append((name.lower().encode("ascii"), value.encode("ascii")))
                if request.url.path == "/access/token":
                    message["headers"].append((b"cache-control", b"no-store"))
                if request.url.path in PUBLIC_PATHS or request.url.path == "/access/token":
                    message["headers"].append((b"x-frame-options", b"DENY"))
            await send(message)

        await self.app(scope, receive, send_response)
