"""Visor de croquis para incrustar en otra web (`GET /_proxy/topos.js`).

    <div id="tc-topos"></div>
    <script src="https://thecrag.adrianlzt.com/_proxy/topos.js"></script>
    <script>tcTopos.render(document.getElementById("tc-topos"), {country: "china", area: "Yangshuo", crag: "Wine Bottle"});</script>

Pide `/_proxy/topos` al origen del propio script y pinta cada croquis: foto de theCrag con las líneas encima (SVG con el
mismo sistema de coordenadas que `data-topodata`), número y grado de cada vía, en verde las encadenadas según 8a.nu
(ámbar con «?» si solo coincide el nombre en otra escuela), naranja las demás. Al tocar una vía se resalta y se ve su
nombre; el botón ⛶ pone el croquis a pantalla completa con pellizco para ampliar y arrastre para mover. Si no
encuentra la escuela en theCrag, deja pegar la URL de theCrag (`opts.onManual(path)` avisa a la web que lo incrusta
por si quiere guardarla; el servidor también la recuerda).
"""

SCRIPT = r"""
(function () {
  "use strict";
  var ORIGIN = (function () {
    var s = document.currentScript && document.currentScript.src;
    try { return new URL(s).origin; } catch (e) { return location.origin; }
  })();
  var CSS = ".tct{font:14px/1.4 system-ui,sans-serif;color:#222}" +
    ".tct-topo{margin:0 0 14px;border:1px solid #ddd;border-radius:8px;overflow:hidden;background:#111}" +
    ".tct-head{display:flex;align-items:center;gap:8px;padding:6px 10px;background:#f4f4f4;color:#222;font-weight:600}" +
    ".tct-head .sp{flex:1}.tct-head button{border:1px solid #bbb;background:#fff;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:14px}" +
    ".tct-wrap{position:relative;overflow:hidden;touch-action:pan-y}.tct-wrap .zoom{position:relative;width:100%}" +
    ".tct-wrap img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:fill;user-select:none;-webkit-user-drag:none;background:#333}" +
    ".tct-wrap svg{position:absolute;left:0;top:0;width:100%;height:100%}" +
    ".tct-wrap .r{cursor:pointer}.tct-wrap .r .ln{fill:none;stroke-width:3;stroke-linejoin:round;stroke-linecap:round}" +
    ".tct-wrap .r .halo{fill:none;stroke:#fff;stroke-width:5;stroke-linejoin:round;stroke-linecap:round;opacity:.8}" +
    ".tct-wrap .r.sel .ln{stroke-width:5}.tct-wrap .r.dim{opacity:.35}" +
    ".tct-wrap .lbl rect{stroke:#000;stroke-width:1}.tct-wrap .lbl text{font:bold 9px Tahoma,Arial;text-anchor:middle;fill:#000}" +
    ".tct-wrap .grd{font:bold 8px Tahoma,Arial;text-anchor:middle;fill:#000;stroke:#fff;stroke-width:2px;paint-order:stroke}" +
    ".tct-info{padding:6px 10px;background:#fff;color:#222;min-height:20px;font-size:13px}" +
    ".tct-info b{margin-right:6px}.tct-badge{display:inline-block;padding:0 6px;border-radius:3px;color:#fff;font-weight:bold;margin-right:6px}" +
    ".tct-legend{font-size:12px;color:#555;margin:4px 0 10px}.tct-legend i{display:inline-block;width:14px;height:4px;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px}" +
    ".tct-list{list-style:none;margin:0;padding:0;background:#fff;border-top:1px solid #eee;max-height:220px;overflow:auto}" +
    ".tct-list li{display:flex;align-items:center;gap:8px;padding:4px 10px;border-bottom:1px solid #f2f2f2;cursor:pointer;font-size:13px}" +
    ".tct-list li.sel{background:#eef7ff}.tct-list .n{min-width:22px;height:18px;line-height:18px;text-align:center;border-radius:3px;font-weight:bold;font-size:11px;color:#000}" +
    ".tct-list .g{min-width:34px;font-weight:bold}.tct-list .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".tct-msg{padding:10px;background:#fff8e1;border:1px solid #f0d080;border-radius:6px}" +
    ".tct-msg input{width:100%;box-sizing:border-box;margin:6px 0;padding:6px;font-size:14px}" +
    ".tct-topo:fullscreen{display:block;overflow:auto;background:#000;color:#ddd}.tct-topo:fullscreen .tct-head{background:#222;color:#ddd}" +
    ".tct-topo:fullscreen .tct-wrap .zoom{transform-origin:0 0}";
  var COLORS = { sent: "#1fbf3f", maybe: "#e6a700", none: "#ff8c1a" };

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    (children || []).forEach(function (c) { e.appendChild(c); });
    return e;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function esc(s) { return String(s == null ? "" : s); }

  function drawTopo(t, opts) {
    var w = t.width || 650, h = t.height || 290;
    var img = el("img", { src: t.image || t.thumb, alt: t.heading || "", loading: "lazy" });
    var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, preserveAspectRatio: "none" });
    var info = el("div", { class: "tct-info" });
    var groups = [];
    var sorted = t.routes.slice().sort(function (a, b) { return (a.sent === "sent") - (b.sent === "sent"); }); // las encadenadas, encima
    sorted.forEach(function (r) {
      var color = COLORS[r.sent || "none"] || COLORS.none;
      var pts = r.points.map(function (p) { return p.x + "," + p.y; }).join(" ");
      var g = svgEl("g", { class: "r" });
      g.appendChild(svgEl("polyline", { class: "halo", points: pts }));
      var ln = svgEl("polyline", { class: "ln", points: pts, stroke: color });
      if (r.sent === "maybe") ln.setAttribute("stroke-dasharray", "6 3");
      g.appendChild(ln);
      r.points.forEach(function (p) {
        if (p.t === "belay" || p.t === "lower") g.appendChild(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: "#fff", stroke: "#1a3ab8", "stroke-width": 2 }));
      });
      var p0 = r.points[0], pl = r.points[r.points.length - 1], pp = r.points.length > 1 ? r.points[r.points.length - 2] : null;
      if (pp && (pp.x !== pl.x || pp.y !== pl.y)) {   // flecha en el final, como theCrag
        var ang = Math.atan2(pl.y - pp.y, pl.x - pp.x) * 180 / Math.PI;
        g.appendChild(svgEl("polygon", { points: "0,0 -9,-4 -9,4", fill: color, stroke: "#fff", "stroke-width": 1,
          transform: "translate(" + pl.x + "," + pl.y + ") rotate(" + ang + ")" }));
      }
      if (p0 && r.num) {
        // Etiqueta bajo el inicio de la vía, pero siempre dentro del lienzo (theCrag también la sube si toca el borde).
        var lw = 14, lh = 13, lx = Math.min(Math.max(p0.x, lw / 2 + 1), w - lw / 2 - 1), ly = Math.min(p0.y + 3, h - lh - 10);
        var fill = r.sent === "sent" ? "#9be89b" : r.sent === "maybe" ? "#ffe08a" : "#ffd24d";
        var lbl = svgEl("g", { class: "lbl" });
        lbl.appendChild(svgEl("rect", { x: lx - lw / 2, y: ly, width: lw, height: lh, rx: 2, fill: fill }));
        var tx = svgEl("text", { x: lx, y: ly + 10 }); tx.textContent = r.num; lbl.appendChild(tx);
        if (r.grade) { var gr = svgEl("text", { class: "grd", x: lx, y: ly + lh + 8 }); gr.textContent = r.grade; lbl.appendChild(gr); }
        g.appendChild(lbl);
      }
      var title = svgEl("title"); title.textContent = "#" + r.num + " " + r.name + " " + r.grade; g.appendChild(title);
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(r, g); });
      svg.appendChild(g); groups.push(g); r._g = g;
    });
    // Lista de vías del croquis: para saber cuál es cuál sin tocar la foto; tocar una la resalta.
    var list = el("ul", { class: "tct-list" });
    t.routes.slice().sort(function (a, b) { return (parseInt(a.num, 10) || 0) - (parseInt(b.num, 10) || 0); }).forEach(function (r) {
      var li = el("li", {}, [el("span", { class: "n", text: r.num || "·" }), el("span", { class: "g", text: r.grade || "" }), el("span", { class: "nm", text: r.name || "" })]);
      li.firstChild.style.background = r.sent === "sent" ? "#9be89b" : r.sent === "maybe" ? "#ffe08a" : "#ffd24d";
      if (r.sent === "sent") li.appendChild(el("span", { class: "tct-badge", text: "✓", style: "background:" + COLORS.sent }));
      if (r.sent === "maybe") li.appendChild(el("span", { class: "tct-badge", text: "?", style: "background:" + COLORS.maybe }));
      li.addEventListener("click", function () { select(r, r._g); });
      r._li = li; list.appendChild(li);
    });
    function select(r, g) {
      var same = g.classList.contains("sel");
      groups.forEach(function (x) { x.classList.remove("sel"); x.classList.toggle("dim", !same && x !== g); });
      t.routes.forEach(function (x) { if (x._li) x._li.classList.toggle("sel", !same && x === r); });
      if (same) { info.textContent = ""; return; }
      g.classList.add("sel");
      if (r._li && r._li.scrollIntoView) r._li.scrollIntoView({ block: "nearest" });
      info.textContent = "";
      var b = el("span", { class: "tct-badge", text: r.sent === "sent" ? "✓" : r.sent === "maybe" ? "?" : "·" });
      b.style.background = COLORS[r.sent || "none"];
      info.appendChild(b);
      info.appendChild(el("b", { text: "#" + r.num + " " + r.name }));
      info.appendChild(document.createTextNode((r.grade || "") + (r.stars ? " " + r.stars : "") + (r.style ? " · " + r.style : "")));
      if (r.sent_info) info.appendChild(document.createTextNode(" · 8a.nu: " + esc(r.sent_info.crag) + " " + esc(r.sent_info.grade) + (r.sent_info.date ? ", " + r.sent_info.date : "")));
      if (r.url) { info.appendChild(document.createTextNode(" ")); info.appendChild(el("a", { href: ORIGIN + r.url, target: "_blank", text: "theCrag ↗" })); }
    }
    svg.addEventListener("click", function () { groups.forEach(function (x) { x.classList.remove("sel", "dim"); }); t.routes.forEach(function (x) { if (x._li) x._li.classList.remove("sel"); }); info.textContent = ""; });
    var zoom = el("div", { class: "zoom" }, [img, svg]);
    zoom.style.aspectRatio = w + " / " + h;   // el hueco de la foto, aunque tarde en cargar
    var wrap = el("div", { class: "tct-wrap" }, [zoom]);
    var fsBtn = el("button", { type: "button", title: "Pantalla completa", text: "⛶" });
    var head = el("div", { class: "tct-head" }, [el("span", { text: t.heading || "" }), el("span", { class: "sp" }), el("span", { text: t.routes.length + " vías", style: "font-weight:normal;color:#666" }), fsBtn]);
    var card = el("div", { class: "tct-topo" }, [head, wrap, info, list]);
    fsBtn.addEventListener("click", function () {
      if (document.fullscreenElement === card) document.exitFullscreen();
      else if (card.requestFullscreen) card.requestFullscreen();
    });
    pinch(wrap, zoom, card);
    return card;
  }

  // Pellizco para ampliar y arrastre para mover, solo a pantalla completa (fuera, la página hace scroll normal).
  function pinch(wrap, zoom, card) {
    var s = 1, tx = 0, ty = 0, pinchD = null, pan = null, drag = null;
    function on() { return document.fullscreenElement === card; }
    function apply() {
      zoom.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + s + ")";
      wrap.style.touchAction = s > 1 ? "none" : "pan-y";   // sin ampliar, un dedo hace scroll de la página
    }
    function dist(a, b) { var dx = a.clientX - b.clientX, dy = a.clientY - b.clientY; return Math.sqrt(dx * dx + dy * dy) || 1; }
    function zoomAt(cx, cy, f) {
      var ns = Math.max(1, Math.min(8, s * f)); if (ns === s) return;
      var r = wrap.getBoundingClientRect(), lx = (cx - r.left - tx) / s, ly = (cy - r.top - ty) / s;
      s = ns; tx = s === 1 ? 0 : cx - r.left - s * lx; ty = s === 1 ? 0 : cy - r.top - s * ly; apply();
    }
    wrap.addEventListener("touchstart", function (e) {
      if (!on()) return;
      if (e.touches.length >= 2) { pinchD = dist(e.touches[0], e.touches[1]); pan = null; e.preventDefault(); }
      else if (s > 1) pan = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }, { passive: false });
    wrap.addEventListener("touchmove", function (e) {
      if (!on()) return;
      if (e.touches.length >= 2 && pinchD) {
        var d = dist(e.touches[0], e.touches[1]);
        zoomAt((e.touches[0].clientX + e.touches[1].clientX) / 2, (e.touches[0].clientY + e.touches[1].clientY) / 2, d / pinchD);
        pinchD = d; e.preventDefault();
      } else if (pan && e.touches.length === 1) {
        tx += e.touches[0].clientX - pan.x; ty += e.touches[0].clientY - pan.y;
        pan = { x: e.touches[0].clientX, y: e.touches[0].clientY }; apply(); e.preventDefault();
      }
    }, { passive: false });
    wrap.addEventListener("touchend", function (e) { if (e.touches.length < 2) pinchD = null; if (!e.touches.length) pan = null; });
    wrap.addEventListener("wheel", function (e) {
      if (!on()) return;
      if (e.ctrlKey) zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.01)); else if (s > 1) { tx -= e.deltaX; ty -= e.deltaY; apply(); } else return;
      e.preventDefault();
    }, { passive: false });
    wrap.addEventListener("mousedown", function (e) { if (on() && s > 1 && e.button === 0) { drag = { x: e.clientX, y: e.clientY }; e.preventDefault(); } });
    document.addEventListener("mousemove", function (e) { if (!drag) return; tx += e.clientX - drag.x; ty += e.clientY - drag.y; drag = { x: e.clientX, y: e.clientY }; apply(); });
    document.addEventListener("mouseup", function () { drag = null; });
    document.addEventListener("fullscreenchange", function () { if (!on()) { s = 1; tx = ty = 0; apply(); } });
  }

  function render(container, opts) {
    opts = opts || {};
    if (!document.getElementById("tct-css")) { var st = el("style", { id: "tct-css" }); st.textContent = CSS; document.head.appendChild(st); }
    container.classList.add("tct");
    container.textContent = "";
    container.appendChild(el("div", { class: "tct-msg", text: "Buscando croquis en theCrag…" }));
    var q = new URLSearchParams({ country: opts.country || "", area: opts.area || "", crag: opts.crag || "" });
    if (opts.tc) q.set("tc", opts.tc);
    fetch(ORIGIN + "/_proxy/topos?" + q.toString()).then(function (r) { return r.json(); }).then(function (d) {
      container.textContent = "";
      if (d.error) {
        var msg = el("div", { class: "tct-msg" });
        msg.appendChild(el("div", { text: d.error === "not_found" ? "No encuentro «" + esc(opts.crag) + "» en theCrag." :
          d.error === "thecrag_blocked" ? "theCrag no responde ahora (nos corta por pedir seguido); prueba en unos minutos." : "Error: " + d.error }));
        var inp = el("input", { type: "url", placeholder: "Pega la URL de la escuela en theCrag" });
        var btn = el("button", { type: "button", text: "Usar esta página" });
        btn.addEventListener("click", function () {
          var m = /thecrag\.[a-z.]+(\/en\/climbing\/[a-z0-9\/_-]+)/i.exec(inp.value || "");
          if (!m) { inp.style.borderColor = "red"; return; }
          var tc = m[1].replace(/\/(maps|photos|topos|routes|ascents)\/?$/, "");
          if (opts.onManual) opts.onManual(tc);
          render(container, Object.assign({}, opts, { tc: tc }));
        });
        msg.appendChild(inp); msg.appendChild(btn);
        container.appendChild(msg);
        return;
      }
      var legend = el("div", { class: "tct-legend" });
      legend.appendChild(el("a", { href: ORIGIN + d.crag.path, target: "_blank", text: (d.crag.name || "Escuela") + " en theCrag ↗" }));
      ["sent", "none", "maybe"].forEach(function (k) {
        var i = el("i"); i.style.background = COLORS[k]; legend.appendChild(i);
        legend.appendChild(document.createTextNode(k === "sent" ? "encadenada (8a.nu)" : k === "maybe" ? "¿misma vía en otra escuela?" : "sin encadenar"));
      });
      container.appendChild(legend);
      if (!d.topos.length) container.appendChild(el("div", { class: "tct-msg", text: "theCrag no tiene croquis de esta escuela." }));
      d.topos.forEach(function (t) { container.appendChild(drawTopo(t, opts)); });
      if (opts.onLoad) opts.onLoad(d);
    }).catch(function (e) {
      container.textContent = ""; container.appendChild(el("div", { class: "tct-msg", text: "No se pudieron cargar los croquis (" + e + ")." }));
    });
  }

  window.tcTopos = { render: render, origin: ORIGIN };
})();
"""
