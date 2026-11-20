"""Marcar en theCrag las vías que Adrián tiene encadenadas según 8a.nu.

Las ascensiones salen del Postgres de 8anu-dashboard (Oracle), que el scraper rellena tres veces al día; desde fuera
se consulta por la API de su Grafana (`/api/ds/query`, que admite lectura anónima). Se guardan en un JSON local
(`TICKS_CACHE`) y se refrescan cada `TICKS_REFRESH_H` horas en segundo plano, así que si Grafana no responde se sigue
con lo último que hubo.

Cruce (`match`): por nombre de vía normalizado (sin acentos ni signos, minúsculas, solo letras latinas y dígitos: en
China theCrag pone «老寿星 Ya Mummas as Old as Dirt» y 8a.nu solo «Ya Mummas as Old as Dirt»; sin artículo inicial).
Si el nombre coincide y además la escuela/sector/área de 8a.nu está entre los nombres de la página de theCrag (migas,
título, cabeceras), o el grado está a un paso o menos, es `sent`; si solo coincide el nombre, `maybe` (misma vía en
otra escuela, probablemente). No se distingue a vista / flash / encadene.

El cliente (`SCRIPT`) junta las vías de la página (filas `.route[data-route-tick]` y croquis `data-topodata`) y los
nombres de escuela, pide `POST /_proxy/ticks` y pinta un ✓ verde (o «?» ámbar) junto al tick de cada fila y en la
etiqueta del croquis (relleno verde y ✓).
"""

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import httpx

from .grades import _SCALES, convert_grade_text

log = logging.getLogger("thecrag-proxy.ticks")

GRAFANA = os.environ.get("TICKS_GRAFANA", "https://8anu.adrianlzt.com").rstrip("/")
DS_UID = os.environ.get("TICKS_DS_UID", "aet0jntondqtcc")
USER_SLUG = os.environ.get("TICKS_USER", "adrian-lopez-lrxzc")
CACHE = Path(os.environ.get("TICKS_CACHE", "~/.local/share/thecrag-proxy/ascents.json")).expanduser()
REFRESH_H = float(os.environ.get("TICKS_REFRESH_H", "6"))

SQL = (
    "select crag_name, sector_name, area_name, zlaggable_name, difficulty, type, to_char(date,'YYYY-MM-DD') as d "
    "from ascents where user_slug = %s and project = false"
)

_FR_INDEX = {g: i for i, (g, _a, _b) in enumerate(_SCALES["fr"])}
_ARTICLES = ("the", "la", "el", "los", "las", "les", "le", "l", "il", "lo", "der", "die", "das")


def norm(s: str) -> str:
    """«老寿星 Ya Mummas as Old as Dirt» → «yamummasasoldasdirt»; «L'Herbolari» → «herbolari»."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    words = re.findall(r"[a-z0-9]+", s)
    if words and words[0] in _ARTICLES and len(words) > 1:
        words = words[1:]
    out = "".join(words)
    if out:
        return out
    return re.sub(r"[\s\W_]+", "", s)  # solo CJK u otros alfabetos: tal cual


def grade_index(grade: str) -> int | None:
    """Posición en la escala francesa; YDS/V se convierten antes. «6a/6a+» → la primera."""
    if not grade:
        return None
    g = convert_grade_text(grade.strip()).split("/")[0].split(" ")[0]
    return _FR_INDEX.get(g)


class Ticks:
    def __init__(self) -> None:
        self.ascents: list[dict] = []
        self.by_name: dict[str, list[dict]] = {}
        self.loaded_at: float = 0.0
        self.source = "none"

    @property
    def enabled(self) -> bool:
        return bool(USER_SLUG)

    def set(self, rows: list[dict], source: str) -> None:
        self.ascents = rows
        self.by_name = {}
        for r in rows:
            r["_name"] = norm(r.get("zlaggable_name", ""))
            r["_places"] = {norm(r.get(k, "")) for k in ("crag_name", "sector_name", "area_name")} - {""}
            r["_grade"] = grade_index(r.get("difficulty", ""))
            self.by_name.setdefault(r["_name"], []).append(r)
        self.loaded_at = time.time()
        self.source = source

    def load_cache(self) -> bool:
        try:
            d = json.loads(CACHE.read_text())
        except (OSError, ValueError):
            return False
        self.set(d["ascents"], "cache")
        self.loaded_at = d.get("at", 0)
        return True

    async def refresh(self, client: httpx.AsyncClient) -> bool:
        body = {
            "queries": [{"refId": "A", "datasource": {"uid": DS_UID}, "rawSql": SQL % f"'{USER_SLUG}'", "format": "table"}],
            "from": "now-1h",
            "to": "now",
        }
        try:
            r = await client.post(f"{GRAFANA}/api/ds/query", json=body, timeout=60)
            r.raise_for_status()
            frame = r.json()["results"]["A"]["frames"][0]
            names = [f["name"] for f in frame["schema"]["fields"]]
            rows = [dict(zip(names, vals)) for vals in zip(*frame["data"]["values"])]
        except Exception as e:  # red, Grafana caída, formato: seguimos con lo que haya
            log.warning("ascensiones de 8a.nu: no se pudieron refrescar (%s)", e)
            return False
        self.set(rows, "grafana")
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"at": self.loaded_at, "ascents": [
                {k: v for k, v in row.items() if not k.startswith("_")} for row in rows
            ]}))
        except OSError as e:
            log.warning("no se pudo guardar %s: %s", CACHE, e)
        log.info("ascensiones de 8a.nu: %d (%s)", len(rows), USER_SLUG)
        return True

    async def run(self, client: httpx.AsyncClient) -> None:
        """Tarea de fondo: carga la caché y refresca cada REFRESH_H horas."""
        self.load_cache()
        while True:
            if time.time() - self.loaded_at > REFRESH_H * 3600 - 60:
                await self.refresh(client)
            await asyncio.sleep(600)

    def match(self, routes: list[dict], places: list[str]) -> dict[str, dict]:
        """routes: [{id, name, grade}]; places: nombres de escuela/área de la página. → {id: {status, ...}}."""
        pl = {norm(p) for p in places} - {""}
        out: dict[str, dict] = {}
        for rt in routes:
            cands = self.by_name.get(norm(rt.get("name", "")))
            if not cands:
                continue
            gi = grade_index(rt.get("grade", ""))
            best, best_score = None, -1
            for c in cands:
                place = any(a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a)) for a in c["_places"] for b in pl)
                grade = gi is not None and c["_grade"] is not None and abs(gi - c["_grade"]) <= 1
                score = (2 if place else 0) + (1 if grade else 0)
                if score > best_score:
                    best, best_score = c, score
            out[str(rt.get("id"))] = {
                "status": "sent" if best_score > 0 else "maybe",
                "date": best.get("d"),
                "crag": best.get("crag_name"),
                "grade": best.get("difficulty"),
            }
        return out


ticks = Ticks()


SCRIPT = """<script>
/* ✓ en las vías encadenadas según 8a.nu. Ver app/ticks.py. */
(function () {
  var CLS = "tcfr-tick";
  var css = document.createElement("style");
  css.textContent = "." + CLS + "{display:inline-block;min-width:14px;padding:0 4px;margin-right:4px;border-radius:3px;" +
    "font-weight:bold;font-size:12px;line-height:16px;text-align:center;color:#fff;background:#1f9d3a;vertical-align:middle}" +
    "." + CLS + ".maybe{background:#d98c00}";
  document.head.appendChild(css);

  function routesOnPage() {
    var seen = {}, routes = [];
    document.querySelectorAll(".route[data-route-tick]").forEach(function (el) {
      try {
        var d = JSON.parse(el.getAttribute("data-route-tick"));
        if (d.id && !seen[d.id]) { seen[d.id] = 1; routes.push({ id: String(d.id), name: d.name || "", grade: (d.gradeAtom || {}).grade || "" }); }
      } catch (e) {}
    });
    document.querySelectorAll(".phototopo[data-topodata]").forEach(function (el) {
      try {
        JSON.parse(el.getAttribute("data-topodata")).forEach(function (r) {
          if (r && r.type === "route" && r.id && !seen[r.id]) { seen[r.id] = 1; routes.push({ id: String(r.id), name: r.name || "", grade: r.grade || "" }); }
        });
      } catch (e) {}
    });
    return routes;
  }

  function placesOnPage() {
    var out = [];
    document.querySelectorAll("a[itemprop=item][data-nid] span, h1 .primary-node-name, h2 .primary-node-name, h3 .primary-node-name, h4 .primary-node-name")
      .forEach(function (el) { var t = el.textContent.trim(); if (t) out.push(t); });
    return out;
  }

  function badge(info) {
    var b = document.createElement("span");
    b.className = CLS + (info.status === "maybe" ? " maybe" : "");
    b.textContent = info.status === "maybe" ? "?" : "\\u2713";
    b.title = "8a.nu: " + (info.crag || "") + " " + (info.grade || "") + (info.date ? ", " + info.date : "") +
      (info.status === "maybe" ? " (mismo nombre en otra escuela)" : "");
    return b;
  }

  function markList(ticks) {
    Object.keys(ticks).forEach(function (id) {
      document.querySelectorAll('.route[data-nid="' + id + '"] .tick').forEach(function (t) {
        if (!t.querySelector("." + CLS)) t.insertBefore(badge(ticks[id]), t.firstChild);
      });
    });
  }

  function markTopo(fsc, ticks) {
    var topo = fsc.querySelector(".phototopo[data-topodata]");
    if (!topo) return;
    var byNum = {};
    try {
      JSON.parse(topo.getAttribute("data-topodata")).forEach(function (r) {
        if (r && r.type === "route" && r.num && ticks[String(r.id)]) byNum[String(r.num)] = ticks[String(r.id)];
      });
    } catch (e) { return; }
    fsc.querySelectorAll(".phototopo svg g.label").forEach(function (g) {
      if (g.querySelector("." + CLS)) return;
      var rect = g.querySelector("rect"), text = g.querySelector("text");
      if (!rect || !text) return;
      var info = byNum[text.textContent.trim()];
      if (!info) return;
      var maybe = info.status === "maybe";
      rect.setAttribute("fill", maybe ? "#ffe08a" : "#9be89b");
      rect.setAttribute("stroke", maybe ? "#b36b00" : "#1f7a2e");
      var x = +rect.getAttribute("x"), y = +rect.getAttribute("y"), w = +rect.getAttribute("width");
      var t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("class", CLS);
      t.setAttribute("x", x + w + 1);
      t.setAttribute("y", y + 5);
      t.setAttribute("style", "font-family: Tahoma, Arial; font-size: 7px; font-weight: bold; fill: " +
        (maybe ? "#b36b00" : "#1f7a2e") + "; stroke: #fff; stroke-width: 2px; paint-order: stroke; pointer-events: none;");
      t.textContent = maybe ? "?" : "\\u2713";
      g.appendChild(t);
    });
  }

  function watchTopos(ticks) {
    document.querySelectorAll(".phototopo-fsc").forEach(function (fsc) {
      markTopo(fsc, ticks);
      var canvas = fsc.querySelector(".phototopo .canvas") || fsc, pending = false;
      new MutationObserver(function () {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () { pending = false; markTopo(fsc, ticks); });
      }).observe(canvas, { childList: true, subtree: true });
    });
  }

  function run() {
    var routes = routesOnPage();
    if (!routes.length) return;
    fetch("/_proxy/ticks", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ routes: routes, places: placesOnPage() }) })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (ticks) { if (Object.keys(ticks).length) { markList(ticks); watchTopos(ticks); } })
      .catch(function () {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
</script>"""


def inject(html: str) -> str:
    """Añade el script antes de `</body>` si la página lista vías."""
    if not ticks.enabled or "data-route-tick" not in html or "</body>" not in html:
        return html
    return html.replace("</body>", SCRIPT + "</body>", 1)
