import httpx
import pytest
from starlette.testclient import TestClient

import app.main as main


SEEN: dict[str, str] = {}


@pytest.fixture
def client(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.thecrag.com"
        assert "authorization" not in request.headers
        assert "tcproxy_auth" not in request.headers.get("cookie", "")
        if request.url.path == "/headers":
            SEEN["raw"] = request.headers.raw
            return httpx.Response(204)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://www.thecrag.com/new"})
        if request.url.path == "/login":
            return httpx.Response(
                200,
                headers=[("set-cookie", "sid=1; Domain=.thecrag.com; Path=/; Secure"), ("content-type", "text/plain")],
                text="ok",
            )
        if request.url.path == "/topo":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text='<a class="badge badge--supporter" title="Croquis" href="/en/supporterbenefits#topofullscreen">x</a>',
            )
        if request.url.path == "/echo":
            SEEN.update(referer=request.headers["referer"], body=request.content.decode(), url=str(request.url))
            return httpx.Response(204)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<a href="https://www.thecrag.com/en/x">x</a><img src="https://image.thecrag.com/a.jpg">'
            '<span class=" gb3">5.11d</span>',
        )

    monkeypatch.setattr(main, "PASSWORD", "secreto")
    real = httpx.AsyncClient

    def fake_client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)
    with TestClient(main.app, base_url="https://thecrag.adrianlzt.com") as c:
        yield c


def test_auth(client):
    r = client.get("/en/x")
    assert r.status_code == 401
    assert r.headers["x-robots-tag"].startswith("noindex")
    r = client.get("/en/x", auth=("adrian", "secreto"))
    assert r.status_code == 200
    assert "tcproxy_auth" in r.cookies
    # Con la cookie ya no hace falta la contraseña.
    assert client.get("/en/x").status_code == 200


def test_reescribe(client):
    r = client.get("/en/x", auth=("a", "secreto"))
    assert 'href="https://thecrag.adrianlzt.com/en/x"' in r.text
    assert "https://image.thecrag.com/a.jpg" in r.text
    assert '<span class=" gb3">7a</span>' in r.text
    assert r.headers["x-robots-tag"].startswith("noindex")


def test_redireccion_y_cookies(client):
    r = client.get("/old", auth=("a", "secreto"), follow_redirects=False)
    assert r.headers["location"] == "https://thecrag.adrianlzt.com/new"
    r = client.get("/login", auth=("a", "secreto"))
    assert "sid=1; Path=/; Secure" in r.headers.get_list("set-cookie")


def test_peticion_hacia_arriba(client):
    r = client.post(
        "/echo",
        auth=("a", "secreto"),
        headers={"referer": "https://thecrag.adrianlzt.com/en/x", "content-type": "application/x-www-form-urlencoded"},
        content="back=https://thecrag.adrianlzt.com/en/y",
    )
    assert r.status_code == 204
    assert SEEN["referer"] == "https://www.thecrag.com/en/x"
    assert SEEN["body"] == "back=https://www.thecrag.com/en/y"


def test_cabeceras_de_navegador(client):
    """El WAF de theCrag bloquea a los clientes que no lo parecen: UA de Chrome, Accept de navegador y nombres de
    cabecera en su grafía (httpx los escribe en minúscula)."""
    SEEN.clear()
    r = client.get(
        "/headers",
        auth=("a", "secreto"),
        headers={"user-agent": "curl/8.5.0", "accept": "*/*", "range": "bytes=0-99"},
    )
    assert r.status_code == 204
    raw = dict(SEEN["raw"])
    assert raw[b"User-Agent"] == main.USER_AGENT.encode()
    assert raw[b"Accept"] == main.BROWSER_HEADERS["accept"].encode()
    assert raw[b"Accept-Language"] == main.BROWSER_HEADERS["accept-language"].encode()
    assert raw[b"Accept-Encoding"] == b"gzip, deflate, br"
    # `range` no está en HEADER_CASE: la grafía se saca igualmente.
    assert raw[b"Range"] == b"bytes=0-99"
    # Ni una cabecera con el nombre en minúscula, que es lo que rechaza el WAF.
    assert [k for k in raw if k[:1].islower()] == []

    # El navegador de verdad conserva lo suyo: no se pisa su user-agent ni su idioma.
    SEEN.clear()
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    r = client.get("/headers", auth=("a", "secreto"), headers={"user-agent": ua, "accept-language": "en-GB,en"})
    assert r.status_code == 204
    raw = dict(SEEN["raw"])
    assert raw[b"User-Agent"] == ua.encode()
    assert raw[b"Accept-Language"] == b"en-GB,en"


def test_croquis_pantalla_completa(client):
    """El botón de supporter pasa a ser el real (`fn-topo-fullscreen`) siempre, no solo con el proxy cerrado."""
    r = client.get("/topo", auth=("a", "secreto"))
    assert 'class="badge badge--supporter fn-topo-fullscreen"' in r.text
    assert 'href="#"' in r.text
    assert "supporterbenefits" not in r.text


def test_robots(client):
    assert "Disallow: /" in client.get("/robots.txt").text


def test_query_codificada(client):
    SEEN.clear()
    r = client.post(
        "/echo?back=https%3A%2F%2Fthecrag.adrianlzt.com%2Fen%2Fy",
        auth=("a", "secreto"),
        headers={"referer": "https://thecrag.adrianlzt.com/"},
    )
    assert r.status_code == 204
    assert SEEN["url"] == "https://www.thecrag.com/echo?back=https%3A%2F%2Fwww.thecrag.com%2Fen%2Fy"


def test_maps_js(client, monkeypatch):
    import app.main as m

    m._static_cache["maps-1.1.62.js"] = b"L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png')"
    r = client.get("/_tcstatic/cids/maps-1.1.62.js")
    assert r.status_code == 200 and "tile.openstreetmap.org" in r.text
    assert client.get("/_tcstatic/cids/common-1.1.78.js").status_code == 404


def test_to_public_codificado():
    import app.main as m

    loc = "/CIDS/cgi-bin/cids.cgi?return=https%3A%2F%2Fwww.thecrag.com%2Fen%2Fx&D%3ALoginTarget=https%3A%2F%2Fwww.thecrag.com%2Fen%2Fx"
    out = m.to_public(loc, "https://thecrag.adrianlzt.com")
    assert "www.thecrag.com" not in out
    assert out.count("https%3A%2F%2Fthecrag.adrianlzt.com%2Fen%2Fx") == 2
