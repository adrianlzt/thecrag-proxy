"""Gesto de zoom del croquis a pantalla completa.

En theCrag el croquis amplía 3x al hacer clic (y otro clic lo deshace): es su `autoZoom`, pensado para un solo toque.
A pantalla completa eso es incómodo, así que el proxy inyecta este script, que mientras el croquis está a pantalla
completa desactiva ese clic y pone lo de siempre: pellizco de dos dedos (o Ctrl+rueda, que es como el navegador
entrega el pellizco del trackpad) para ampliar alrededor del punto señalado, y arrastre para mover.

Dos cosas del JS de theCrag obligan a engancharse con `capture` y a cortar la propagación:

- `PhotoTopo.autoZoom` es el clic que amplía. Se sustituye por un no-op al entrar, y se restaura al salir (también
  neutraliza el `touchstart` que theCrag engancha al entrar en pantalla completa y que llama a `autoZoom`).
- Su `scrubMove`/`scrubEnd` van en `document` con `capture`, así que ven el toque antes que este script: al arrastrar
  lo tomarían por un «scrub», que acaba seleccionando la ruta que hubiera bajo el dedo. Por eso el `touchstart` de
  aquí corta la propagación cuando el croquis está ampliado o hay dos dedos, y así theCrag ni se entera.

Fuera de pantalla completa no se toca nada: el croquis se comporta como en theCrag.
"""

SCRIPT = """<script>
/* Zoom del croquis a pantalla completa: pellizco / Ctrl+rueda para ampliar y arrastre para mover (en vez del clic
   que amplía 3x de theCrag). Ver app/fullscreen.py. */
(function () {
  var MAX = 8;                                  // ampliación máxima
  var SEL = ".phototopo-fsc";                   // contenedor que theCrag pone a pantalla completa

  function dist(a, b) {
    var dx = a.clientX - b.clientX, dy = a.clientY - b.clientY;
    return Math.sqrt(dx * dx + dy * dy) || 1;
  }

  function canvasOf(fsc) { return fsc.querySelector(".phototopo .canvas"); }
  // Lo que se amplía: la imagen y el SVG del croquis, no el `.canvas`, porque dentro de él está la barra de botones
  // (`.actionable`: Hide, Legend, pantalla completa), que debe quedarse quieta.
  function targetsOf(fsc) {
    var c = canvasOf(fsc);
    return c ? [].slice.call(c.querySelectorAll(":scope > img, :scope > svg")) : [];
  }
  function topoOf(fsc) {
    return window.jQuery ? window.jQuery(fsc).find(".phototopo").data("phototopo") : null;
  }

  // Al salir de pantalla completa: desenganchar todo y devolver el croquis a como estaba.
  function stop(fsc) {
    if (!fsc || !fsc.dataset.fsZoom) return;
    (fsc._fsZoomOff || []).forEach(function (o) { o.t.removeEventListener(o.type, o.fn, o.opts); });
    fsc._fsZoomOff = null;
    delete fsc.dataset.fsZoom;

    var canvas = canvasOf(fsc);
    if (canvas) canvas.classList.remove("zoomed");
    targetsOf(fsc).forEach(function (el) {
      el.style.transform = "";
      el.style.transformOrigin = "";
    });
    if (canvas && topoOf(fsc)) canvas.classList.add("autozoom");
    var topo = topoOf(fsc);
    if (topo && topo._fsZoomAuto) {   // el clic que amplía vuelve a ser el de theCrag
      topo.autoZoom = topo._fsZoomAuto;
      topo._fsZoomAuto = null;
    }
  }

  function init(fsc) {
    if (fsc.dataset.fsZoom) return;
    var canvas = canvasOf(fsc);
    if (!canvas) return;
    fsc.dataset.fsZoom = "1";

    var s = 1, tx = 0, ty = 0;   // ampliación y desplazamiento actuales
    var topo = topoOf(fsc);
    if (topo) {                  // el clic y el tap de theCrag ya no amplían
      topo._fsZoomAuto = topo.autoZoom;
      topo.autoZoom = function () {};
    }

    function on(t, type, fn, opts) {
      t.addEventListener(type, fn, opts);
      fsc._fsZoomOff.push({ t: t, type: type, fn: fn, opts: opts });
    }
    fsc._fsZoomOff = [];

    // Esquina del croquis sin transformar, en coordenadas de pantalla. El `.canvas` no se transforma (solo su
    // imagen y su SVG), así que su rectángulo es el punto fijo al que referir la ampliación.
    function origin() {
      var r = canvas.getBoundingClientRect();
      return { x: r.left, y: r.top };
    }

    function apply() {
      targetsOf(fsc).forEach(function (el) {
        el.style.transformOrigin = "0 0";
        el.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + s + ")";
      });
      canvas.classList.toggle("zoomed", s > 1);
      canvas.classList.remove("autozoom");
      if (s > 1) {   // la imagen grande solo hace falta ampliada
        canvas.querySelectorAll("img[data-big]").forEach(function (img) {
          img.src = img.dataset.big;
          img.removeAttribute("data-big");
        });
      }
    }

    // Amplía alrededor del punto (cx, cy) de la pantalla, que se queda donde está.
    function zoomAt(cx, cy, factor) {
      var ns = Math.max(1, Math.min(MAX, s * factor));
      if (ns === s) return;
      var o = origin();
      var lx = (cx - o.x - tx) / s, ly = (cy - o.y - ty) / s;
      s = ns;
      tx = s === 1 ? 0 : cx - o.x - s * lx;
      ty = s === 1 ? 0 : cy - o.y - s * ly;
      apply();
    }

    var pinch = null, pan = null;

    on(fsc, "touchstart", function (e) {
      if (e.touches.length >= 2) {              // pellizco: theCrag no debe verlo
        pinch = { d: dist(e.touches[0], e.touches[1]) };
        pan = null;
        e.preventDefault();
        e.stopPropagation();
      } else if (s > 1) {                       // ampliado: un dedo mueve el croquis
        pan = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        e.stopPropagation();
      }
    }, { capture: true, passive: false });

    on(fsc, "touchmove", function (e) {
      if (e.touches.length >= 2 && pinch) {
        var d = dist(e.touches[0], e.touches[1]);
        zoomAt((e.touches[0].clientX + e.touches[1].clientX) / 2,
               (e.touches[0].clientY + e.touches[1].clientY) / 2, d / pinch.d);
        pinch.d = d;
        e.preventDefault();
        e.stopPropagation();
      } else if (pan && e.touches.length === 1) {
        tx += e.touches[0].clientX - pan.x;
        ty += e.touches[0].clientY - pan.y;
        pan = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        apply();
        e.preventDefault();
        e.stopPropagation();
      }
    }, { capture: true, passive: false });

    on(fsc, "touchend", function (e) {
      if (e.touches.length < 2) pinch = null;
      if (!e.touches.length) pan = null;
      else if (s > 1) pan = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }, { capture: true, passive: false });

    on(fsc, "wheel", function (e) {
      if (e.ctrlKey) {                          // pellizco del trackpad
        // El pellizco llega como Ctrl+rueda con deltas pequeños y seguidos; se acota el salto por evento para que
        // no dé un tirón si el sistema manda un delta grande.
        var f = Math.max(0.9, Math.min(1.1, Math.exp(-e.deltaY * 0.01)));
        zoomAt(e.clientX, e.clientY, f);
      } else if (s > 1) {                       // dos dedos desplazan
        tx -= e.deltaX;
        ty -= e.deltaY;
        apply();
      } else {
        return;                                 // sin ampliar, la rueda sigue siendo de la página
      }
      e.preventDefault();
      e.stopPropagation();
    }, { capture: true, passive: false });

    var drag = null;
    on(fsc, "mousedown", function (e) {
      // Los botones de la barra (Hide, Legend, salir de pantalla completa) no arrastran: van dentro del lienzo.
      if (e.target.closest && e.target.closest(".actionable")) return;
      if (s > 1 && e.button === 0) {
        drag = { x: e.clientX, y: e.clientY };
        e.preventDefault();
        e.stopPropagation();
      }
    }, { capture: true });
    on(document, "mousemove", function (e) {
      if (!drag) return;
      tx += e.clientX - drag.x;
      ty += e.clientY - drag.y;
      drag = { x: e.clientX, y: e.clientY };
      apply();
      e.preventDefault();
    }, { capture: true });
    on(document, "mouseup", function () { drag = null; }, { capture: true });
  }

  document.addEventListener("fullscreenchange", function () {
    var fsc = document.fullscreenElement;
    if (fsc && fsc.matches && fsc.matches(SEL)) init(fsc);
    else document.querySelectorAll(SEL).forEach(stop);
  });
})();
</script>"""


def inject(html: str) -> str:
    """Añade el script antes de `</body>` si la página trae algún croquis."""
    if "phototopo-fsc" not in html or "</body>" not in html:
        return html
    return html.replace("</body>", SCRIPT + "</body>", 1)
