"""Conversión de grados americanos (YDS y escala V) a francés (y Font) en el HTML de theCrag.

Usa la conversión universal de theCrag: en su artículo de grados cada sistema es una columna de intervalos sobre
el mismo eje de dificultad (`scales.json`, sacado con scripts/extract_scales.py). Un grado se convierte al grado
destino que más se solapa con su intervalo (si empatan, el más alto).
"""

import json
import re
from functools import cache
from pathlib import Path

_SCALES = {
    sid: [(g, a, b) for g, a, b in rows]
    for sid, rows in json.loads(Path(__file__).with_name("scales.json").read_text()).items()
}
_SPAN = {sid: {g: (a, b) for g, a, b in rows} for sid, rows in _SCALES.items()}


def _best(sid: str, a: float, b: float) -> str:
    """Grado de `sid` que más se solapa con [a, b)."""
    rows = _SCALES[sid]
    best, best_len = None, 0.0
    for g, ga, gb in rows:
        overlap = min(b, gb) - max(a, ga)
        if overlap > 0 and overlap >= best_len - 1e-9:
            best, best_len = g, overlap
    if best is None:
        return rows[0][0] if b <= rows[0][1] else rows[-1][0]
    return best


def _mid(a: float, b: float) -> float:
    return (a + b) / 2


def _join(parts: list[str]) -> str:
    out: list[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    return "/".join(out)


# ---------------------------------------------------------------- YDS

# 5.9, 5.9+, 5.10a, 5.10a/b, 5.10a-b, 5.10, 5.10-, 5.10+. Ni dentro de números (65.651, 5.10.3) ni en rutas.
YDS_RE = re.compile(r"(?<![\w.:/])5\.(1[0-5]|[0-9])((?:[abcd](?:[/-][abcd])?)|[+-](?!\d))?(?![\w]|\.\d)")


@cache
def yds_to_fr(num: int, suffix: str) -> str | None:
    if num < 10:
        span = _SPAN["yds"].get(f"5.{num}")
        if not span:
            return None
        a, b = span
        if suffix == "+":
            a = _mid(a, b)
        elif suffix == "-":
            b = _mid(a, b)
        return _best("fr", a, b)
    if suffix and suffix[0] in "abcd":
        letters = [suffix[0]] + ([suffix[2]] if len(suffix) == 3 else [])
        spans = [_SPAN["yds"].get(f"5.{num}{x}") for x in letters]
        if not all(spans):
            return None
        return _join([_best("fr", *s) for s in spans])
    letters = {"": "ad", "-": "ab", "+": "cd"}[suffix]
    lo, hi = _SPAN["yds"].get(f"5.{num}{letters[0]}"), _SPAN["yds"].get(f"5.{num}{letters[1]}")
    if not lo or not hi:
        return None
    return _best("fr", lo[0], hi[1])


def _yds_sub(m: re.Match) -> str:
    return yds_to_fr(int(m.group(1)), m.group(2) or "") or m.group(0)


def convert_yds(text: str) -> str:
    return YDS_RE.sub(_yds_sub, text)


# ---------------------------------------------------------------- escala V (bloque)

V_RE = re.compile(r"(?<![\w.:/])V(B|1[0-7]|[0-9])([+-](?!\d))?(?:([-/])V?(1[0-7]|[0-9]))?(?![\w+]|\.\d)")


@cache
def v_to_font(base: str, mod: str, hi: str) -> str | None:
    if base == "B" or not hi:
        span = _SPAN["bldv"].get(f"V{base}{mod}")
        return _best("fb", *span) if span else None
    lo_span, hi_span = _SPAN["bldv"].get(f"V{base}{mod}"), _SPAN["bldv"].get(f"V{hi}")
    if not lo_span or not hi_span:
        return None
    return _join([_best("fb", *lo_span), _best("fb", *hi_span)])


def _v_sub(m: re.Match) -> str:
    return v_to_font(m.group(1), m.group(2) or "", m.group(4) or "") or m.group(0)


def convert_grade_text(text: str) -> str:
    """Texto que se sabe que es un grado (span gbN, JSON del topo): YDS y V."""
    return V_RE.sub(_v_sub, convert_yds(text))


# ---------------------------------------------------------------- HTML

# La página tiene grados americanos si alguno sale en un span de grado, en el topo o en el título de la vía.
# Solo entonces se convierte también el texto libre (histogramas, «Grade citation», descripciones…).
def _detector(grade: str) -> re.Pattern:
    return re.compile(rf'class="[^"]*\bgb\d+[^"]*"[^>]*>\s*{grade}|"grade":"{grade}|<title>\s*{grade}')


_HAS_YDS = _detector(r"5\.\d")
_HAS_V = _detector(r"V[B\d]")
_GB_SPAN = re.compile(r'(<span\b[^>]*\bclass="[^"]*\bgb\d+[^"]*"[^>]*>)([^<]*)(</span>)')
_TOPO_GRADE = re.compile(r'("grade"\s*:\s*")([^"]*)(")')
_TOKENS = re.compile(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1\s*>|<[^>]*>", re.S | re.I)
# Atributos visibles. Nada de href/src/data-route-tick (enlaces y datos que se reenvían al apuntar ascensiones).
_ATTRS = re.compile(r"""(\s(?:title|content|alt|aria-label|data-original-title)=)(["'])(.*?)\2""", re.S | re.I)
_TOPO_ATTR = re.compile(r"""(\sdata-topodata=)(["'])(.*?)\2""", re.S)


def convert_html(html: str) -> str:
    yds, v = bool(_HAS_YDS.search(html)), bool(_HAS_V.search(html))
    if not yds and not v:
        return html

    def text(t: str) -> str:
        if yds:
            t = convert_yds(t)
        if v:
            t = V_RE.sub(_v_sub, t)
        return t

    def tag(t: str) -> str:
        t = _ATTRS.sub(lambda m: m.group(1) + m.group(2) + text(m.group(3)) + m.group(2), t)
        return _TOPO_ATTR.sub(
            lambda m: m.group(1)
            + m.group(2)
            + _TOPO_GRADE.sub(lambda g: g.group(1) + convert_grade_text(g.group(2)) + g.group(3), m.group(3))
            + m.group(2),
            t,
        )

    html = _GB_SPAN.sub(lambda m: m.group(1) + convert_grade_text(m.group(2)) + m.group(3), html)
    out: list[str] = []
    pos = 0
    for m in _TOKENS.finditer(html):
        out.append(text(html[pos : m.start()]))
        tok = m.group(0)
        out.append(tok if m.group(1) or tok.startswith("<!--") else tag(tok))
        pos = m.end()
    out.append(text(html[pos:]))
    return "".join(out)
