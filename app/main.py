"""Proxy inverso de www.thecrag.com que muestra los grados americanos en escala francesa.

https://thecrag.adrianlzt.com/<ruta> sirve https://www.thecrag.com/<ruta>: reescribe los enlaces absolutos, las
redirecciones y las cookies para que la navegación siga en el proxy, y convierte los grados del HTML (app/grades.py).
CSS, JS e imágenes de static.thecrag.com / image.thecrag.com se cargan directamente de theCrag.

Uso personal: con PROXY_PASSWORD pide autenticación básica (una vez; luego queda una cookie de un año) y todas las
respuestas llevan X-Robots-Tag: noindex.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from .grades import convert_html

UPSTREAM = os.environ.get("UPSTREAM", "https://www.thecrag.com").rstrip("/")
UPSTREAM_HOST = httpx.URL(UPSTREAM).host
PASSWORD = os.environ.get("PROXY_PASSWORD", "")
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))
AUTH_COOKIE = "tcproxy_auth"
ROBOTS = "noindex, nofollow, noarchive, nosnippet"

# El WAF de theCrag (Cloudflare) bloquea a los clientes que no parecen un navegador. Con el user-agent de httpx o de
# curl devuelve 403 «You are unable to access»; con uno de Chrome o Firefox, 200. El que manda el navegador del
# cliente se respeta (el del proxy solo sustituye al de un cliente HTTP); THE_CRAG_UA cambia el de por defecto.
USER_AGENT = os.environ.get(
    "THE_CRAG_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
)
# Cabeceras que completa el proxy cuando el cliente no las manda (o manda las de un cliente HTTP): sin unas
# Accept/Accept-Language de navegador el WAF también bloquea, aunque el user-agent sea bueno.
BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "es-ES,es;q=0.9,en;q=0.8",
}
# httpx pone en minúscula los nombres de cabecera y con eso el WAF devuelve 403 aunque los valores sean de Chrome:
# http/1.1 los distingue (HTTP/2 los normaliza, por eso con h2 el case da igual). Aquí se les devuelve la grafía de
# un navegador; las que no estén en la lista caen en `_header_name`, que las capitaliza igual.
HEADER_CASE = {
    b"accept": b"Accept",
    b"accept-encoding": b"Accept-Encoding",
    b"accept-language": b"Accept-Language",
    b"connection": b"Connection",
    b"content-length": b"Content-Length",
    b"content-type": b"Content-Type",
    b"cookie": b"Cookie",
    b"host": b"Host",
    b"if-modified-since": b"If-Modified-Since",
    b"if-none-match": b"If-None-Match",
    b"origin": b"Origin",
    b"pragma": b"Pragma",
    b"referer": b"Referer",
    b"user-agent": b"User-Agent",
    b"x-requested-with": b"X-Requested-With",
}

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "host", "content-length",
}  # fmt: skip
# Cabeceras de la petición que se reenvían a theCrag. El resto no: ni las del túnel/Traefik (IP y país del cliente,
# cf-*, x-forwarded-*) ni las de navegador (sec-ch-*, sec-fetch-*), que no cuadran con un cliente HTTP de servidor.
FORWARD_REQUEST = {
    "user-agent", "accept", "accept-language", "cookie", "referer", "origin", "content-type",
    "x-requested-with", "if-none-match", "if-modified-since", "range", "cache-control", "pragma",
}  # fmt: skip
DROP_RESPONSE = HOP_BY_HOP | {"content-encoding", "date", "server", "set-cookie", "report-to", "nel", "alt-svc", "strict-transport-security"}
TEXT_TYPES = ("text/", "application/json", "application/javascript", "application/x-javascript", "+json", "+xml")

# https://www.thecrag.com, //www.thecrag.com, https:\/\/thecrag.com (JSON)… pero no static./image.thecrag.com.
_ABS_RE = re.compile(r"(https?:)?(//|\\/\\/)(?:www\.)?thecrag\.com(?![\w.-])", re.I)
# La misma URL codificada en un parámetro (p. ej. `LoginTarget=https%3A%2F%2Fwww.thecrag.com%2F…` del login): sin
# esto, tras iniciar sesión theCrag manda al navegador a www.thecrag.com y la sesión se queda fuera del proxy.
_ABS_ENC_RE = re.compile(r"https?%3A%2F%2F(?:www\.)?thecrag\.com(?![\w.-])", re.I)
_COOKIE_DOMAIN_RE = re.compile(r";\s*domain=[^;]*", re.I)

# El JS de mapas se sirve desde el proxy para cambiar el fondo por defecto («OSM (theCrag)»): sus teselas vienen de
# maps.thecrag.com, que solo las da a www.thecrag.com (403 con el Referer del proxy). Se cambian por las de
# tile.openstreetmap.org, que son el mismo mapa.
STATIC = "https://static.thecrag.com"
_MAPS_JS_RE = re.compile(r"https://static\.thecrag\.com/cids/(maps-[\d.]+\.js)")
_MAPS_NAME_RE = re.compile(r"maps-[\d.]+\.js")
THECRAG_TILES = "https://maps.thecrag.com/{z}/{x}/{y}.png"
OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_static_cache: dict[str, bytes] = {}

# Croquis a pantalla completa (ventaja de supporter; Adrián lo es, pero su sesión no llega a reconocerse por el proxy y
# el proxy ya solo es accesible con su contraseña). Sin sesión de supporter theCrag pinta el botón como un enlace a
# supporterbenefits; se convierte en el botón real (`fn-topo-fullscreen`), que common.js engancha al cargar.
_TOPO_FS_RE = re.compile(r'<a class="badge badge--supporter"([^>]*?)href="[^"]*supporterbenefits#topofullscreen"')


def _auth_token() -> str:
    return hmac.new(PASSWORD.encode(), b"thecrag-proxy", hashlib.sha256).hexdigest()


def _authorized(request: Request) -> bool:
    if not PASSWORD:
        return True
    if hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _auth_token()):
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except ValueError:
            return False
        return hmac.compare_digest(pw.encode(), PASSWORD.encode())
    return False


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def to_public(text: str, origin: str) -> str:
    scheme, host = origin.split("://", 1)

    def sub(m: re.Match) -> str:
        return (f"{scheme}:" if m.group(1) else "") + m.group(2) + host

    return _ABS_ENC_RE.sub(quote(origin, safe=""), _ABS_RE.sub(sub, text))


def to_upstream(text: str, origin: str) -> str:
    """Origen del proxy → theCrag, también URL-codificado (parámetros de query y formularios)."""
    return text.replace(origin, UPSTREAM).replace(quote(origin, safe=""), quote(UPSTREAM, safe=""))


def _header_name(name: str) -> str:
    """`accept-encoding` → `Accept-Encoding`: la grafía que escribe un navegador y que el WAF de theCrag exige."""
    return HEADER_CASE.get(name.encode(), b"-".join(p.capitalize() for p in name.encode().split(b"-"))).decode()


def _request_headers(request: Request, origin: str) -> dict[str, str]:
    headers = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk not in FORWARD_REQUEST:
            continue
        if lk == "cookie":
            v = "; ".join(c for c in v.split("; ") if not c.startswith(AUTH_COOKIE + "="))
            if not v:
                continue
        elif lk in ("origin", "referer"):
            v = to_upstream(v, origin)
        headers[lk] = v
    # El cliente puede ser otro cliente HTTP (curl, un test, el navegador con una extensión): se completa lo que
    # espere un navegador, sin pisar lo que sí haya mandado.
    for k, v in BROWSER_HEADERS.items():
        headers.setdefault(k, v)
    if headers.get("accept") == "*/*":
        headers["accept"] = BROWSER_HEADERS["accept"]
    if not headers.get("user-agent") or headers["user-agent"].lower().startswith(("curl/", "python-", "httpx/")):
        headers["user-agent"] = USER_AGENT
    headers["accept-encoding"] = "gzip, deflate, br"
    # httpx respeta la grafía de las claves del dict, así que aquí se les devuelve la de un navegador. Starlette
    # entrega las cabeceras del cliente en minúscula, de ahí que haya que rehacerlas todas.
    return {_header_name(k): v for k, v in headers.items()}


def _response_headers(upstream: httpx.Response, origin: str) -> list[tuple[bytes, bytes]]:
    out = []
    for k, v in upstream.headers.multi_items():
        lk = k.lower()
        if lk in DROP_RESPONSE or lk.startswith("cf-"):
            continue
        if lk in ("location", "content-location", "link", "refresh"):
            v = to_public(v, origin)
        out.append((lk.encode(), v.encode("latin-1", "replace")))
    for cookie in upstream.headers.get_list("set-cookie"):
        out.append((b"set-cookie", _COOKIE_DOMAIN_RE.sub("", cookie).encode("latin-1", "replace")))
    out.append((b"x-robots-tag", ROBOTS.encode()))
    return out


def _is_text(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(t) or ct.endswith(t) for t in TEXT_TYPES)


async def robots(_: Request) -> Response:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"x-robots-tag": ROBOTS})


async def healthz(_: Request) -> Response:
    return PlainTextResponse("ok")


async def maps_js(request: Request) -> Response:
    name = request.path_params["name"]
    if not _MAPS_NAME_RE.fullmatch(name):
        return PlainTextResponse("no", status_code=404)
    if name not in _static_cache:
        r = await request.app.state.client.get(f"{STATIC}/cids/{name}")
        if r.status_code != 200:
            return PlainTextResponse("upstream", status_code=502)
        _static_cache[name] = r.text.replace(THECRAG_TILES, OSM_TILES).encode()
    return Response(
        _static_cache[name],
        media_type="text/javascript",
        headers={"cache-control": "public, max-age=86400", "x-robots-tag": ROBOTS},
    )


async def proxy(request: Request) -> Response:
    if not _authorized(request):
        return PlainTextResponse(
            "Autenticación requerida",
            status_code=401,
            headers={"www-authenticate": 'Basic realm="thecrag-proxy"', "x-robots-tag": ROBOTS},
        )
    origin = _origin(request)
    client: httpx.AsyncClient = request.app.state.client
    url = UPSTREAM + request.scope["raw_path"].decode("latin-1")
    if request.url.query:
        url += "?" + to_upstream(request.url.query, origin)
    body = await request.body()
    req_type = request.headers.get("content-type", "")
    if body and (_is_text(req_type) or "urlencoded" in req_type):
        body = to_upstream(body.decode("utf-8", "surrogateescape"), origin).encode("utf-8", "surrogateescape")

    async with request.app.state.sem:
        req = client.build_request(request.method, url, headers=_request_headers(request, origin), content=body)
        upstream = await client.send(req, stream=True)
        headers = _response_headers(upstream, origin)
        set_auth = PASSWORD and request.cookies.get(AUTH_COOKIE) != _auth_token()
        if set_auth:
            headers.append(
                (
                    b"set-cookie",
                    f"{AUTH_COOKIE}={_auth_token()}; Max-Age=31536000; Path=/; HttpOnly; Secure; SameSite=Lax".encode(),
                )
            )
        ctype = upstream.headers.get("content-type", "")
        if not _is_text(ctype):
            resp = StreamingResponse(
                upstream.aiter_bytes(), status_code=upstream.status_code, background=BackgroundTask(upstream.aclose)
            )
            resp.raw_headers = headers
            return resp
        try:
            await upstream.aread()
        finally:
            await upstream.aclose()

    text = to_public(upstream.text, origin)
    if ctype.startswith("text/html"):
        text = _MAPS_JS_RE.sub(r"/_tcstatic/cids/\1", text)
        if PASSWORD:  # solo con el proxy cerrado con contraseña
            text = _TOPO_FS_RE.sub(r'<a class="badge badge--supporter fn-topo-fullscreen"\1href="#"', text)
        text = convert_html(text)
    content = text.encode(upstream.encoding or "utf-8", "replace")
    resp = Response(content, status_code=upstream.status_code)
    resp.raw_headers = [h for h in headers if h[0] != b"content-type"] + [
        (b"content-type", ctype.encode()),
        (b"content-length", str(len(content)).encode()),
    ]
    return resp


@asynccontextmanager
async def lifespan(app: Starlette):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, http2=False
    ) as client:
        app.state.client = client
        app.state.sem = asyncio.Semaphore(MAX_CONCURRENCY)
        yield


METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
app = Starlette(
    routes=[
        Route("/robots.txt", robots),
        Route("/healthz", healthz),
        Route("/_tcstatic/cids/{name}", maps_js),
        Route("/{path:path}", proxy, methods=METHODS),
    ],
    lifespan=lifespan,
)
