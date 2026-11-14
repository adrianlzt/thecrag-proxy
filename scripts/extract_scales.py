"""Extrae las escalas de grados de https://www.thecrag.com/en/article/grades.

El artículo dibuja una columna por sistema con un <div> por grado posicionado en vertical (top/height): la misma
altura en dos columnas es el mismo grado según la conversión universal de theCrag. Imprime JSON
{sistema: [[grado, inicio, fin], ...]}.

    curl -s https://www.thecrag.com/en/article/grades > grades.html
    python scripts/extract_scales.py grades.html > app/scales.json
"""

import json
import re
import sys

WANTED = {"yds", "fr", "bldv", "fb"}

html = open(sys.argv[1], encoding="utf-8").read()
out = {}
for table in re.findall(r"<table.*?</table>", html, flags=re.S):
    ids = re.findall(r'<th id="grade-system-([^"]+)"', table)
    cols = re.split(r'<div class="grades"', table)[1:]
    if not ids or len(cols) != len(ids):
        continue
    for sid, col in zip(ids, cols):
        if sid not in WANTED:
            continue
        out[sid] = [
            [g.strip(), float(t), float(t) + float(h)]
            for t, h, g in re.findall(r'top:([\d.]+)px;[^"]*?height:([\d.]+)px;[^>]*>([^<]+)</div>', col)
        ]
json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
