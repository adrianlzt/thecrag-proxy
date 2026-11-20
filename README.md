# thecrag-proxy

Proxy inverso de [theCrag](https://www.thecrag.com) que enseña los grados americanos en escala francesa.
Cambiando `www.thecrag.com` por `thecrag.adrianlzt.com` en cualquier URL se ve la misma página, con YDS
(5.10a…) pasado a francés (6a…) y la escala V de bloque a Font (V4 → 6B+).

Ejemplo: https://thecrag.adrianlzt.com/en/climbing/united-states/red-river-gorge/route/17075671
(también responde en `thecrag.k8s.adrianlzt.com`).

## Cómo funciona

- **Por qué no una cookie**: theCrag enseña cada zona en el sistema de su país («grade context») y solo los usuarios
  registrados pueden elegir otro en su perfil. Para anónimos no hay cookie, cabecera ni parámetro que lo cambie, y la
  API de conversión (`/api/grade`) pide sesión. Así que el proxy reescribe el HTML.
- **Tabla de conversión**: la de theCrag. Su artículo [Grades and Grade Conversions](https://www.thecrag.com/en/article/grades)
  dibuja cada sistema como una columna de intervalos sobre el mismo eje; `scripts/extract_scales.py` los saca a
  `app/scales.json` y cada grado se convierte al que más se solapa (`app/grades.py`). Regenerar:
  `curl -s https://www.thecrag.com/en/article/grades > /tmp/g.html && uv run python scripts/extract_scales.py /tmp/g.html > app/scales.json`
- **Qué se convierte** (solo en páginas con grados americanos en un `span.gbN`, en el topo o en el título): los
  spans de grado, `<title>`, atributos visibles (`title`, `content`, `alt`…), los grados del JSON `data-topodata` del
  topo y el texto libre (histogramas, «Grade citation», descripciones). No se tocan enlaces (`/with-grade/YDS:5.10a`),
  `<script>` ni `data-route-tick` (datos que se envían al apuntar una ascensión). Las zonas que ya son francesas
  (España, Francia…) salen intactas.
- **Navegación**: `https://www.thecrag.com` (y `//`, `\/\/` en JSON) pasa a ser el origen del proxy en HTML, JSON,
  JS y CSS; también `Location`, y `Set-Cookie` pierde el `Domain=` para quedarse en el dominio del proxy. Hacia
  arriba se hace lo contrario con `Referer`, `Origin`, query y cuerpos de formularios. CSS, JS e imágenes vienen de
  `static.thecrag.com`/`image.thecrag.com` directamente.
- **No indexable**: solo habla con `www.thecrag.com`, responde `X-Robots-Tag: noindex` y `robots.txt` con
  `Disallow: /`, y limita a 6 peticiones simultáneas a theCrag (`MAX_CONCURRENCY`). Va sin contraseña; con
  `PROXY_PASSWORD` pide autenticación básica la primera vez (luego cookie `tcproxy_auth` de un año).
- **Se hace pasar por un navegador**: el WAF de theCrag (Cloudflare) responde 403 «You are unable to access» a quien
  no lo parezca. Comprueba tres cosas, y falla cualquiera de ellas: el `User-Agent` (el de Chrome o Firefox pasa, el
  de `curl`, `python-httpx` o `HeadlessChrome` no), unas `Accept`/`Accept-Language` de navegador, y —esto es lo
  fino— que los **nombres** de cabecera vayan en su grafía (`User-Agent`, no `user-agent`), que en HTTP/1.1 se
  distinguen. httpx los escribe en minúscula y Starlette los entrega así, de modo que el proxy los reescribe antes de
  enviarlos (`HEADER_CASE`) y completa lo que falte (`BROWSER_HEADERS`, `USER_AGENT`), sin pisar lo que sí mande el
  navegador del cliente. Con `Accept-Encoding` anuncia `br`, así que la dependencia `brotli` es necesaria para
  descomprimirlo. La `THE_CRAG_UA` cambia el user-agent por defecto si theCrag vuelve a apretar.

- **Mapas**: el fondo por defecto («OSM (theCrag)») pide teselas a `maps.thecrag.com`, que no las da fuera de
  www.thecrag.com. El proxy sirve `maps-*.js` desde `/_tcstatic/cids/` con esa URL cambiada por
  `tile.openstreetmap.org` (mismo mapa); el resto del JS sigue viniendo de static.thecrag.com.
- **Croquis a pantalla completa**: en theCrag es una ventaja de los supporters (de pago); sin sesión de supporter el
  botón lleva a `supporterbenefits#topofullscreen`. Adrián es supporter pero su sesión no se reconocía por el proxy,
  así que el proxy convierte ese enlace en el botón real (`fn-topo-fullscreen`), que usa la API Fullscreen del
  navegador. Como es un extra de supporter y el proxy es de uso exclusivo suyo, se aplica siempre (también sin
  contraseña).
- **Zoom del croquis a pantalla completa**: theCrag amplía 3x al hacer clic (y otro clic lo deshace), que a pantalla
  completa es incómodo. El proxy inyecta un script (`app/fullscreen.py`) que, solo mientras el croquis está a
  pantalla completa, desactiva ese clic y pone lo de siempre: pellizco de dos dedos (o Ctrl+rueda, que es como el
  navegador entrega el pellizco del trackpad) para ampliar alrededor del punto señalado, y arrastre para mover. Al
  salir, el croquis vuelve a como estaba. El clic normal sigue seleccionando la vía.
- **Grados en el croquis**: el proxy inyecta otro script (`app/fullscreen_grades.py`) que pone el grado (ya en
  francés, del `data-topodata`) encima de cada número del croquis, o debajo si choca con el de al lado; a pantalla
  completa y fuera de ella.
- **✓ de lo encadenado según 8a.nu** (`app/ticks.py`): las ascensiones de Adrián salen del Postgres de
  8anu-dashboard (Oracle) vía la API de su Grafana (`/api/ds/query`, lectura anónima), se guardan en
  `~/.local/share/thecrag-proxy/ascents.json` y se refrescan cada 6 h en segundo plano (`TICKS_GRAFANA`,
  `TICKS_DS_UID`, `TICKS_USER`, `TICKS_CACHE`, `TICKS_REFRESH_H`; con `TICKS_USER` vacío se desactiva). En cada página
  con vías, un script manda a `POST /_proxy/ticks` las vías (filas y croquis) y los nombres de escuela de la página; el
  cruce es por nombre normalizado (sin acentos, solo latino, sin artículo inicial) y da `sent` si además coincide la
  escuela/sector/área de 8a.nu o el grado (±1), o `maybe` si solo el nombre. Pinta ✓ verde (o «?» ámbar) junto al tick
  de cada fila y en la etiqueta del croquis. No distingue a vista / flash / encadene; excluye proyectos.
- **Croquis para el proxy de 8a.nu** (`app/topos.py`, `app/topos_js.py`): `GET /_proxy/topos?country=china&area=Yangshuo&crag=Wine%20Bottle`
  (los nombres como los da 8a.nu; `tc=/en/climbing/…` fija la página a mano) devuelve la escuela de theCrag y sus
  croquis: foto, tamaño, y cada vía con número, nombre, grado en francés, puntos del trazo y `sent` (`sent`/`maybe`/
  `null`, con `ticks.match`). La escuela se busca bajando por la jerarquía (país → área por nombre → escuela; si no
  está, subáreas en anchura), con cada página guardada para siempre en SQLite (`TOPOS_DB`), ≥4 s entre páginas
  (`TOPOS_PACE`), 20 páginas por búsqueda como mucho y 10 min de pausa si theCrag corta. Los croquis de una escuela
  se guardan 72 h. En theCrag los croquis suelen estar en los sectores, no en la escuela: si la página de la escuela
  no tiene, se miran hasta 8 hijos directos (`TOPOS_CHILD_MAX`; `sectors` y `partial` en la respuesta), y con
  `sector=<sectorName de 8a.nu>` solo el hijo con ese nombre. CORS abierto. `/_proxy/topos.js` es el visor para incrustar (`tcTopos.render(el, {country, area,
  crag})`): líneas verdes las encadenadas, naranja el resto, ámbar discontinuo las dudosas; el grosor de la línea es
  la popularidad según **8a.nu** (`app/eight.py`): la escuela se busca en 8a.nu por nombre (`cragautocomplete`, o
  `crag_slug=`/`cragSlug` si la web lo sabe), se listan sus vías con `totalAscents` (API pública
  `outdoor/v1/web/zlaggables/{cat}/{país}?cragSlug=…`, 150 por página, caché 24 h en la tabla `eight`) y se cruzan por
  nombre normalizado (sumando variantes de grado ±2); `ascents` son las de 8a.nu, `pop` 0–100 es la raíz de
  ascensiones/máximo, `in8a=false` si la vía no está, y `ascents_tc` las de theCrag. Si 8a.nu no tiene la escuela o
  no casa ninguna vía, se usa theCrag («Relative popularity (59) - 45 ascents»); `popularity` dice cuál; número y grado en cada vía; filtros «Todas / Populares / Encadenadas /
  Populares pendientes» (popular = el tercio más repetido de su croquis, mínimo 3; el filtro se recuerda en
  localStorage y atenúa las demás líneas) y lista de vías con ascensiones, ordenada por ellas cuando hay filtro;
  tocar una vía la resalta; ⛶ pantalla completa con pellizco/arrastre; el contenedor se desplaza por sí mismo (75 vh);
  si no encuentra la escuela deja pegar la URL de theCrag. Las fotos se cargan de image.thecrag.com directamente.

Limitaciones: solo YDS y V (UIAA, británico, Ewbank… se quedan como estén); el contenido que el JS de theCrag pinte
a partir de JSON sin pasar por el HTML seguirá en el sistema original; el login con Google/Facebook vuelve a
`www.thecrag.com` (el de usuario y contraseña sí funciona por el proxy). El WAF de theCrag bloquea navegadores
headless (`HeadlessChrome`), así que las pruebas automáticas de navegador no sirven; el proxy lo sortea con las
cabeceras, no ejecutando un navegador.

## Desarrollo

```sh
uv run pytest -q
PROXY_PASSWORD=prueba uv run uvicorn app.main:app --port 8471   # http://localhost:8471/en/climbing/...
```

Sin `PROXY_PASSWORD` no pide contraseña.

## Despliegue en arc2 (el que se usa)

theCrag (su WAF de Cloudflare) bloqueó la IP de Oracle a las pocas horas; desde casa (arc2) no. Así que el proxy
corre en arc2 como servicio de usuario (`deploy/thecrag-proxy.service`, `127.0.0.1:8471`, sin contraseña:
`~/.config/thecrag-proxy.env` está vacío desde 2026-11-16; para volver a cerrarlo, `PROXY_PASSWORD` sacada de gopass
`thecrag-proxy/auth`) y se
publica por el túnel de Cloudflare de arc2 (`cloudflared.service`, gestionado desde el panel de Zero Trust):
hostname público `thecrag.adrianlzt.com` → `http://localhost:8471`.

```sh
cp deploy/thecrag-proxy.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now thecrag-proxy
git pull && systemctl --user restart thecrag-proxy   # nueva versión
```

## Despliegue (k3s de Oracle, bloqueado por theCrag)

- Cada push a `main` pasa los tests y publica `ghcr.io/adrianlzt/thecrag-proxy:{main,<sha>}` (arm64) con
  `.github/workflows/image.yml`.
- Sin contraseña. Para volver a ponerla: hay un Secret `thecrag-proxy` con `PROXY_PASSWORD` (gopass
  `thecrag-proxy/auth`); basta con añadir al contenedor `envFrom: [{secretRef: {name: thecrag-proxy}}]`.
- Manifiestos en `deploy/k8s.yaml` (namespace `thecrag-proxy`, Traefik + Let's Encrypt, un Ingress por dominio):
  `KUBE_ADMIN=1 kubectl --context oracle-frankfurt-nixos-ts apply -f deploy/k8s.yaml`
- Nueva versión: `KUBE_ADMIN=1 kubectl --context oracle-frankfurt-nixos-ts -n thecrag-proxy rollout restart deploy/thecrag-proxy`
- `thecrag.k8s.adrianlzt.com` va por el comodín `*.k8s`. El Ingress de `thecrag.adrianlzt.com` sobra mientras ese
  nombre vaya por el túnel de arc2.
