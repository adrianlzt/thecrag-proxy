"""Ascensiones por vía según 8a.nu, para la popularidad de los croquis (`/_proxy/topos`).

8a.nu lista las vías de una escuela con su `totalAscents` en su API pública (la misma que usa su web, sin login):
`/api/unification/outdoor/v1/web/zlaggables/{sportclimbing|bouldering}/{país}?cragSlug=…&pageIndex=…&pageSize=150`.
La escuela se busca por nombre con `/api/dotnet/search/cragautocomplete` (o llega su `cragSlug`). El WAF de 8a.nu
pide un User-Agent de navegador. Se guarda en SQLite (`TOPOS_DB`, tabla `eight`) `EIGHT_TTL` horas.
"""

import json
import logging
import math
import os
import re
import time

import httpx

from .ticks import grade_index, norm

log = logging.getLogger("thecrag-proxy.eight")

BASE = os.environ.get("EIGHT_BASE", "https://www.8a.nu").rstrip("/")
TTL = float(os.environ.get("EIGHT_TTL", "24")) * 3600
MAX_PAGES = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
CATS = {0: "sportclimbing", 1: "bouldering"}


def _slug(country: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", (country or "").strip().lower().replace(" ", "-"))


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    r = await client.get(BASE + path, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


async def find_crag(client: httpx.AsyncClient, country: str, area: str, crag: str) -> dict | None:
    """La escuela de 8a.nu con ese nombre (y área si hay varias): {cragSlug, cragName, countrySlug, category, …}."""
    d = await _get(client, "/api/dotnet/search/cragautocomplete", {"query": crag, "countrySlug": _slug(country), "pageSize": 10})
    hits = [h for h in d.get("hits") or [] if h.get("cragSlug")]
    want, want_area = norm(crag), norm(area)
    same = [h for h in hits if norm(h.get("cragName") or "") == want] or hits[:1]
    if not same:
        return None
    same.sort(key=lambda h: (want_area and norm(h.get("areaName") or "") == want_area, h.get("totalAscents") or 0), reverse=True)
    return same[0]


async def routes(client: httpx.AsyncClient, db, country: str, area: str, crag: str, crag_slug: str = "") -> dict | None:
    """{crag: {name, slug, url, totalAscents}, routes: [{name, grade, ascents, sector, path}]} o None si 8a.nu no la tiene."""
    key = "|".join((_slug(country), norm(area), norm(crag), crag_slug))
    db.execute("create table if not exists eight (key text primary key, at real, data text)")
    row = db.execute("select at, data from eight where key=?", (key,)).fetchone()
    if row and time.time() - row[0] < TTL:
        cached = json.loads(row[1])
        if not cached or not cached.get("routes") or "path" in cached["routes"][0]:  # las de antes no traen la ruta
            return cached
    try:
        if crag_slug:
            hit = {"cragSlug": crag_slug, "cragName": crag, "countrySlug": _slug(country), "category": 0}
        else:
            hit = await find_crag(client, country, area, crag)
        data = None
        if hit:
            cat, cs = CATS.get(hit.get("category"), "sportclimbing"), hit.get("countrySlug") or _slug(country)
            items, page = [], 0
            while page < MAX_PAGES:
                d = await _get(client, f"/api/unification/outdoor/v1/web/zlaggables/{cat}/{cs}",
                               {"cragSlug": hit["cragSlug"], "pageIndex": page, "pageSize": 150})
                items += d.get("items") or []
                if not (d.get("pagination") or {}).get("hasNext"):
                    break
                page += 1
            data = {
                "crag": {"name": hit.get("cragName"), "slug": hit["cragSlug"], "url": f"{BASE}/crags/{cat}/{cs}/{hit['cragSlug']}",
                         "totalAscents": sum(i.get("totalAscents") or 0 for i in items)},
                "routes": [{"name": i.get("zlaggableName") or "", "grade": i.get("difficulty") or "", "ascents": i.get("totalAscents") or 0,
                            "sector": i.get("sectorName") or "",
                            "path": f"/crags/{cat}/{cs}/{hit['cragSlug']}/sectors/{i.get('sectorSlug')}/routes/{i.get('zlaggableSlug')}"
                            if i.get("sectorSlug") and i.get("zlaggableSlug") else ""} for i in items],
            }
    except (httpx.HTTPError, ValueError) as e:
        log.warning("8a.nu no responde para «%s»: %s", crag, e)
        return json.loads(row[1]) if row else None  # mejor lo de antes que nada
    db.execute("insert or replace into eight values (?,?,?)", (key, time.time(), json.dumps(data)))
    db.commit()
    return data


def apply(topos: list[dict], eight: dict | None) -> bool:
    """Pone en cada vía de los croquis sus ascensiones en 8a.nu (`ascents`, la de theCrag queda en `ascents_tc`) y
    `pop` 0–100 relativo a la más repetida (raíz cuadrada, para que una vía muy repetida no deje al resto en nada).
    Cruce por nombre normalizado; si 8a.nu tiene la misma vía con variantes del nombre o en dos sectores, se suman las
    de grado parecido (±2). Devuelve False (y no toca nada) si 8a.nu no tiene la escuela o no casa ninguna vía."""
    if not eight or not eight.get("routes"):
        return False
    by_name: dict[str, list[dict]] = {}
    for r in eight["routes"]:
        by_name.setdefault(norm(r["name"]), []).append(r)
    found = {}
    for t in topos:
        for r in t["routes"]:
            n = norm(r["name"])
            cands = by_name.get(n) or []
            if not cands and len(n) >= 6:  # «Wet Nelly (aka …)» / nombres con prefijo
                cands = [x for k, xs in by_name.items() if len(k) >= 6 and (k in n or n in k) for x in xs]
            gi = grade_index(r.get("grade") or "")
            if gi is not None:
                close = [c for c in cands if (cg := grade_index(c["grade"])) is None or abs(cg - gi) <= 2]
                cands = close
            if cands:
                found[id(r)] = sum(c["ascents"] for c in cands)
                best = max(cands, key=lambda c: c["ascents"])
                if best.get("path"):
                    r["url8a"] = best["path"]  # su página en 8a.nu, para apuntarla
    if not found:
        return False
    top = max(found.values()) or 1
    for t in topos:
        for r in t["routes"]:
            r["ascents_tc"] = r.get("ascents")
            r["in8a"] = id(r) in found
            r["ascents"] = found.get(id(r), 0)
            r["pop"] = round(100 * math.sqrt(r["ascents"] / top))
    return True
