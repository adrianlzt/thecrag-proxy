"""Grados junto a los números del croquis a pantalla completa.

En el croquis theCrag solo pinta el número de cada vía (`<g class="label gbN"><rect/><text>num</text></g>` en el SVG);
el grado está en la lista de debajo, que a pantalla completa no se ve. Este script, solo mientras el croquis está a
pantalla completa, añade encima de cada etiqueta un texto con el grado de esa vía, sacado del `data-topodata` del
croquis (que el proxy ya ha pasado a francés). Si en un grupo de etiquetas hay dos pegadas en la misma fila, la
segunda lleva el grado debajo para que no se monten. Al salir de pantalla completa se quitan.

theCrag vuelve a pintar el SVG al cambiar de tamaño (y entrar en pantalla completa es un cambio de tamaño), así que
un MutationObserver los vuelve a poner cuando el SVG cambia.
"""

SCRIPT = """<script>
/* Grados en las etiquetas del croquis a pantalla completa. Ver app/fullscreen_grades.py. */
(function () {
  var SEL = ".phototopo-fsc";
  var NS = "http://www.w3.org/2000/svg";
  var CLS = "tcfr-grade";

  function grades(fsc) {
    var topo = fsc.querySelector(".phototopo[data-topodata]");
    var map = {};
    if (!topo) return map;
    try {
      JSON.parse(topo.getAttribute("data-topodata")).forEach(function (r) {
        if (r && r.type === "route" && r.num && r.grade) map[String(r.num)] = r.grade;
      });
    } catch (e) {}
    return map;
  }

  function clear(fsc) {
    fsc.querySelectorAll("." + CLS).forEach(function (n) { n.remove(); });
  }

  function annotate(fsc) {
    var map = fsc._tcfrGrades || (fsc._tcfrGrades = grades(fsc));
    var labels = [].slice.call(fsc.querySelectorAll(".phototopo svg g.label"));
    var boxes = [];
    labels.forEach(function (g) {
      if (g.querySelector("." + CLS)) return;
      var rect = g.querySelector("rect"), text = g.querySelector("text");
      if (!rect || !text) return;
      var grade = map[text.textContent.trim()];
      if (!grade) return;
      boxes.push({ g: g, x: +rect.getAttribute("x"), y: +rect.getAttribute("y"),
                   w: +rect.getAttribute("width"), h: +rect.getAttribute("height"), grade: grade });
    });
    // Arriba por defecto; abajo si a la izquierda, en la misma fila y pegada, hay otra que ya lo lleva arriba.
    boxes.sort(function (a, b) { return a.y - b.y || a.x - b.x; });
    boxes.forEach(function (b, i) {
      var prev = boxes[i - 1];
      b.below = !!(prev && !prev.below && Math.abs(prev.y - b.y) < 1 && b.x - prev.x < b.w + 8);
      var t = document.createElementNS(NS, "text");
      t.setAttribute("class", CLS);
      t.setAttribute("x", b.x + b.w / 2);
      t.setAttribute("y", b.below ? b.y + b.h + 7 : b.y - 2);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("style", "font-family: Tahoma, Arial; font-size: 7px; font-weight: bold; fill: #000;" +
        " stroke: #fff; stroke-width: 2px; paint-order: stroke; pointer-events: none;");
      t.textContent = b.grade;
      b.g.appendChild(t);
    });
  }

  function start(fsc) {
    if (fsc._tcfrObs) return;
    annotate(fsc);
    var canvas = fsc.querySelector(".phototopo .canvas") || fsc;
    var pending = false;
    fsc._tcfrObs = new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () { pending = false; annotate(fsc); });
    });
    fsc._tcfrObs.observe(canvas, { childList: true, subtree: true });
  }

  function stop(fsc) {
    if (fsc._tcfrObs) { fsc._tcfrObs.disconnect(); fsc._tcfrObs = null; }
    clear(fsc);
  }

  document.addEventListener("fullscreenchange", function () {
    var fsc = document.fullscreenElement;
    if (fsc && fsc.matches && fsc.matches(SEL)) start(fsc);
    else document.querySelectorAll(SEL).forEach(stop);
  });
})();
</script>"""


def inject(html: str) -> str:
    """Añade el script antes de `</body>` si la página trae algún croquis."""
    if "phototopo-fsc" not in html or "</body>" not in html:
        return html
    return html.replace("</body>", SCRIPT + "</body>", 1)
