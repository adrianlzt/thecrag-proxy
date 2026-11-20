"""Croquis de theCrag para otras webs (el proxy de 8a.nu): `GET /_proxy/topos` y el visor `/_proxy/topos.js`.

Dada una escuela de 8a.nu (`country`, `area`, `crag`, como los nombra 8a.nu) busca la página de theCrag bajando por
su jerarquía —país → área con ese nombre → escuela con ese nombre, y si no está, por las subáreas en anchura— y
devuelve sus croquis: foto, tamaño, y las vías con sus puntos (`data-topodata`), grado en francés y si Adrián la
tiene encadenada según 8a.nu (`ticks.match`). Si la búsqueda falla, `tc=/en/climbing/...` fija la página a mano.

theCrag corta (403) si se le piden páginas seguidas, así que cada página se pide una vez y se guarda para siempre en
SQLite (`TOPOS_DB`: enlaces de cada página y escuela encontrada por clave); entre peticiones a theCrag hay al menos
`PACE` segundos, y una búsqueda no pide más de `MAX_PAGES` páginas. Los croquis de una escuela se guardan `TOPOS_TTL`
horas. Las fotos se sirven desde image.thecrag.com directamente (el navegador las carga igual que en theCrag).
"""

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx

from .grades import convert_grade_text
from .ticks import norm, ticks

log = logging.getLogger("thecrag-proxy.topos")

UPSTREAM = os.environ.get("UPSTREAM", "https://www.thecrag.com").rstrip("/")
DB = Path(os.environ.get("TOPOS_DB", "~/.local/share/thecrag-proxy/topos.db")).expanduser()
PACE = float(os.environ.get("TOPOS_PACE", "4"))
MAX_PAGES = int(os.environ.get("TOPOS_MAX_PAGES", "20"))
COOLDOWN = float(os.environ.get("TOPOS_COOLDOWN", "600"))  # tras un corte de theCrag, ni una petición en este tiempo
TTL = float(os.environ.get("TOPOS_TTL", "72")) * 3600
CHILD_MAX = int(os.environ.get("TOPOS_CHILD_MAX", "8"))
CACHE_V = 3  # formato de la caché de croquis: subir cuando cambie parse_topos  # sectores hijos que se miran si la escuela no tiene croquis
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}
# Enlaces de navegación de cada página, que no son hijos.
_NAV = re.compile(r"/(maps|contributors|forum|photos|topos|webcovers|ascents|guidebooks|favorites|guide|weather|"
                  r"accommodation|routes|climbers|search|locate|edit|add|map|guidebook|gear|list|discussion|stream)(/|$)")
_LINK = re.compile(r'<a[^>]+href="(/en/climbing/[^"#?]+)"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")
_TOPO = re.compile(r"""<div class="phototopo"([^>]*)data-topodata='([^']*)'[^>]*>""")
_ATTR = re.compile(r"""data-([a-z-]+)=['"]([^'"]*)['"]""")
_IMG = re.compile(r'<img[^>]+src="(https://image\.thecrag\.com/[^"]+)"(?:[^>]+data-big="(https://image\.thecrag\.com/[^"]+)")?')
_HEAD = re.compile(r'<span class="primary-node-name">([^<]*)</span>')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_H1 = re.compile(r"<h1[^>]*>.*?<span class=\"primary-node-name\">([^<]*)</span>", re.S)
_BLOCKED = ("Attention Required", "swamp.processing.thecrag.com", "You are unable to access")
# Popularidad de cada vía en su fila: «Relative popularity (59) - 45 ascents» (0–100 dentro de la escuela, y ascensiones).
_POP = re.compile(r'route/(\d+)/ascents"[^>]*title="Relative popularity \((\d+)\) - ([\d,]+) ascents?"')


class Blocked(Exception):
    pass


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("create table if not exists pages (path text primary key, at real, links text)")
    c.execute("create table if not exists crags (key text primary key, path text, name text, at real, source text)")
    c.execute("create table if not exists topos (path text primary key, at real, data text)")
    return c


def _clean(t: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", _TAG.sub("", t))).strip()


def parse_links(page_html: str) -> list[dict]:
    """Hijos con nombre de una página de área/país: [{path, name}], sin navegación ni vías."""
    out, seen = [], set()
    for path, text in _LINK.findall(page_html):
        if "/route/" in path or _NAV.search(path) or path in seen:
            continue
        name = _clean(text)
        if not name or name.isdigit():
            continue
        seen.add(path)
        out.append({"path": path, "name": re.sub(r"\s*/\s*[\d.]+\s*$", "", name)})  # «Wine Bottle / 2.4» → sin el número
    return out


def parse_pops(page_html: str) -> dict[str, tuple[int, int]]:
    """id de vía → (popularidad relativa 0–100, ascensiones) según theCrag."""
    return {rid: (int(pop), int(asc.replace(",", ""))) for rid, pop, asc in _POP.findall(page_html)}


def parse_topos(page_html: str, page_path: str) -> list[dict]:
    topos = []
    pops = parse_pops(page_html)
    for m in _TOPO.finditer(page_html):
        attrs = dict(_ATTR.findall(m.group(1)))
        try:
            data = json.loads(html.unescape(m.group(2)))
        except ValueError:
            continue
        tail = page_html[m.end(): m.end() + 3000]
        # theCrag multiplica cada punto por data-view-scale al dibujar sobre un viewBox de width×height.
        try:
            sc = float(attrs.get("view-scale") or 1) or 1.0
        except ValueError:
            sc = 1.0
        img = _IMG.search(tail)
        heads = _HEAD.findall(page_html[: m.start()])
        routes = []
        for r in data:
            if not isinstance(r, dict) or r.get("type") != "route" or not r.get("points"):
                continue
            pts = []
            for p in str(r["points"]).split(","):
                bits = p.strip().split()
                if len(bits) >= 2:
                    try:
                        pts.append({"x": round(float(bits[0]) * sc, 1), "y": round(float(bits[1]) * sc, 1), "t": bits[2] if len(bits) > 2 else ""})
                    except ValueError:
                        pass
            if pts:
                pop = pops.get(str(r.get("id")))
                routes.append({
                    "id": str(r.get("id")), "num": str(r.get("num") or ""), "name": html.unescape(str(r.get("name") or "")),
                    "pop": pop[0] if pop else None, "ascents": pop[1] if pop else None,
                    "grade": convert_grade_text(str(r.get("grade") or "")), "stars": r.get("stars") or "",
                    "style": r.get("style") or "", "url": r.get("url") or "", "points": pts,
                })
        topos.append({
            "tid": attrs.get("tid"), "heading": html.unescape(heads[-1]) if heads else "",
            "width": float(attrs.get("width") or 0), "height": float(attrs.get("height") or 0),
            "image": (img.group(2) or img.group(1)) if img else None, "thumb": img.group(1) if img else None,
            "routes": routes,
        })
    return [t for t in topos if t["image"] and t["routes"]]


def page_name(page_html: str) -> str:
    """«酒瓶山 Wine Bottle, Sport climbing | theCrag» → «酒瓶山 Wine Bottle»."""
    m = _TITLE.search(page_html)
    return _clean(m.group(1)).split(",")[0].split("|")[0].strip() if m else ""


class Topos:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_fetch = 0.0
        self.blocked_until = 0.0

    async def fetch(self, client: httpx.AsyncClient, path: str) -> str:
        """Una página de theCrag, con ritmo. Lanza Blocked si el WAF corta."""
        if time.time() < self.blocked_until:
            raise Blocked("en pausa tras un corte de theCrag")
        async with self.lock:
            wait = PACE - (time.time() - self.last_fetch)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_fetch = time.time()
            r = await client.get(UPSTREAM + path, headers=HEADERS, follow_redirects=False, timeout=30)
        if r.status_code in (403, 429, 503) or any(b in r.text[:3000] for b in _BLOCKED) or (
            300 <= r.status_code < 400 and "swamp" in r.headers.get("location", "")
        ):
            self.blocked_until = time.time() + COOLDOWN
            log.warning("theCrag corta (%s) en %s: pausa de %d s", r.status_code, path, COOLDOWN)
            raise Blocked(f"{r.status_code} {path}")
        r.raise_for_status()
        return r.text

    async def links(self, client: httpx.AsyncClient, path: str, budget: list[int]) -> list[dict]:
        db = _db()
        row = db.execute("select links from pages where path=?", (path,)).fetchone()
        if row:
            return json.loads(row[0])
        if budget[0] <= 0:
            raise Blocked("presupuesto de páginas agotado")
        budget[0] -= 1
        links = parse_links(await self.fetch(client, path))
        db.execute("insert or replace into pages values (?,?,?)", (path, time.time(), json.dumps(links)))
        db.commit()
        return links

    async def find(self, client: httpx.AsyncClient, country: str, area: str, crag: str) -> dict | None:
        """Página de theCrag de una escuela de 8a.nu. País → área → escuela, bajando por subáreas si hace falta."""
        key = "|".join((norm(country), norm(area), norm(crag)))
        db = _db()
        row = db.execute("select path, name from crags where key=?", (key,)).fetchone()
        if row:
            return {"path": row[0], "name": row[1]}
        want, want_area = norm(crag), norm(area)
        start = "/en/climbing/" + re.sub(r"[^a-z0-9-]", "", country.lower().replace(" ", "-"))
        budget = [MAX_PAGES]

        def hit(links: list[dict]) -> dict | None:
            for l in links:
                n = norm(l["name"])
                if n == want or (len(want) >= 5 and (n.endswith(want) or n.startswith(want))) or l["path"].rsplit("/", 1)[-1] == want:
                    return l
            return None

        queue = [start]
        if want_area:
            top = await self.links(client, start, budget)
            if (h := hit(top)):
                return self._save(db, key, h)
            areas = [l for l in top if norm(l["name"]) == want_area or want_area in norm(l["name"])]
            queue = [l["path"] for l in areas] or [start]
        seen = set()
        while queue:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            try:
                links = await self.links(client, path, budget)
            except Blocked as e:
                log.warning("búsqueda de «%s» parada: %s", crag, e)
                break
            if (h := hit(links)):
                return self._save(db, key, h)
            queue.extend(l["path"] for l in links if l["path"] not in seen and l["path"].startswith(path.rsplit("/area/", 1)[0]))
        return None

    def _save(self, db: sqlite3.Connection, key: str, link: dict) -> dict:
        db.execute("insert or replace into crags values (?,?,?,?,?)", (key, link["path"], link["name"], time.time(), "auto"))
        db.commit()
        return {"path": link["path"], "name": link["name"]}

    def set_manual(self, country: str, area: str, crag: str, path: str) -> None:
        db = _db()
        key = "|".join((norm(country), norm(area), norm(crag)))
        db.execute("insert or replace into crags values (?,?,?,?,?)", (key, path, "", time.time(), "manual"))
        db.execute("delete from topos where path=?", (path,))
        db.commit()

    async def topos(self, client: httpx.AsyncClient, path: str) -> dict:
        """Croquis de una página y sus hijos directos (sectores), de la caché o de theCrag."""
        db = _db()
        row = db.execute("select at, data from topos where path=?", (path,)).fetchone()
        if row and time.time() - row[0] < TTL:
            cached = json.loads(row[1])
            if cached.get("v") == CACHE_V:  # las entradas de versiones anteriores se vuelven a pedir
                return cached
        try:
            page = await self.fetch(client, path)
        except Blocked:
            if row:  # theCrag corta: mejor lo guardado de una versión anterior que nada
                stale = json.loads(row[1])
                stale["stale"] = True
                stale.setdefault("children", [])
                return stale
            raise
        children = [l for l in parse_links(page) if l["path"].startswith(path + "/")]
        data = {"v": CACHE_V, "name": page_name(page), "topos": parse_topos(page, path), "children": children}
        db.execute("insert or replace into topos values (?,?,?)", (path, time.time(), json.dumps(data)))
        db.commit()
        return data

    async def with_sectors(self, client: httpx.AsyncClient, path: str, sector: str = "") -> dict:
        """Los croquis de la escuela; si pide un sector, los de ese hijo; si la escuela no tiene (en theCrag suelen
        estar en los sectores), los de hasta CHILD_MAX hijos. `partial` si theCrag cortó a medias."""
        data = await self.topos(client, path)
        out = {"name": data["name"], "topos": list(data["topos"]), "sectors": [], "partial": bool(data.get("stale"))}
        children = data.get("children") or []
        want = norm(sector)
        if want:
            picked = [c for c in children if norm(c["name"]) == want] or [c for c in children if want in norm(c["name"]) or norm(c["name"]) in want]
        else:
            picked = [] if data["topos"] else children[:CHILD_MAX]
        for c in picked:
            try:
                sub = await self.topos(client, c["path"])
            except Blocked:
                out["partial"] = True
                break
            for t in sub["topos"]:
                t = dict(t)
                t["heading"] = c["name"] + (" · " + t["heading"] if t["heading"] and norm(t["heading"]) != norm(c["name"]) else "")
                out["topos"].append(t)
            out["sectors"].append({"name": c["name"], "path": c["path"], "topos": len(sub["topos"])})
        if want and picked:
            out["topos"] = [t for t in out["topos"] if t not in data["topos"]] or out["topos"]
        return out


topos = Topos()


async def lookup(client: httpx.AsyncClient, country: str, area: str, crag: str, tc: str = "", sector: str = "") -> dict:
    """Respuesta de /_proxy/topos: escuela, croquis con ✓ y estado."""
    if tc:
        if not re.fullmatch(r"/en/climbing/[a-z0-9/_-]+", tc):
            return {"error": "bad tc"}
        topos.set_manual(country, area, crag, tc)
        found = {"path": tc, "name": ""}
    else:
        try:
            found = await topos.find(client, country, area, crag)
        except Blocked as e:
            return {"error": "thecrag_blocked", "detail": str(e)}
    if not found:
        return {"error": "not_found", "crag": crag, "area": area, "country": country}
    try:
        data = await topos.with_sectors(client, found["path"], sector)
    except Blocked as e:
        return {"error": "thecrag_blocked", "detail": str(e), "crag": found}
    places = [crag, area, country, sector, found.get("name") or "", data.get("name") or ""] + [x["name"] for x in data["sectors"]]
    for t in data["topos"]:
        marks = ticks.match([{"id": r["id"], "name": r["name"], "grade": r["grade"]} for r in t["routes"]], places + [t["heading"]])
        for r in t["routes"]:
            m = marks.get(r["id"])
            r["sent"] = m["status"] if m else None
            r["sent_info"] = m
    return {"crag": {"path": found["path"], "name": data.get("name") or found.get("name"), "url": UPSTREAM + found["path"]},
            "topos": data["topos"], "sectors": data["sectors"], "partial": data["partial"]}
