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
- **No es un proxy abierto**: solo habla con `www.thecrag.com`, pide contraseña (autenticación básica la primera
  vez; luego cookie `tcproxy_auth` de un año), responde `X-Robots-Tag: noindex` y `robots.txt` con `Disallow: /`, y
  limita a 6 peticiones simultáneas a theCrag (`MAX_CONCURRENCY`).

Limitaciones: solo YDS y V (UIAA, británico, Ewbank… se quedan como estén); el contenido que el JS de theCrag pinte
a partir de JSON sin pasar por el HTML seguirá en el sistema original; el login con Google/Facebook vuelve a
`www.thecrag.com` (el de usuario y contraseña sí funciona por el proxy). El WAF de theCrag bloquea navegadores
headless (`HeadlessChrome`), así que las pruebas automáticas de navegador no sirven.

## Desarrollo

```sh
uv run pytest -q
PROXY_PASSWORD=prueba uv run uvicorn app.main:app --port 8471   # http://localhost:8471/en/climbing/...
```

Sin `PROXY_PASSWORD` no pide contraseña.

## Despliegue (k3s de Oracle)

- Cada push a `main` pasa los tests y publica `ghcr.io/adrianlzt/thecrag-proxy:{main,<sha>}` (arm64) con
  `.github/workflows/image.yml`.
- Contraseña en gopass `thecrag-proxy/auth` (usuario cualquiera). Secret:
  `KUBE_ADMIN=1 kubectl --context oracle-frankfurt-nixos-ts -n thecrag-proxy create secret generic thecrag-proxy --from-literal=PROXY_PASSWORD="$(gopass show -o thecrag-proxy/auth)"`
- Manifiestos en `deploy/k8s.yaml` (namespace `thecrag-proxy`, Traefik + Let's Encrypt, un Ingress por dominio):
  `KUBE_ADMIN=1 kubectl --context oracle-frankfurt-nixos-ts apply -f deploy/k8s.yaml`
- Nueva versión: `KUBE_ADMIN=1 kubectl --context oracle-frankfurt-nixos-ts -n thecrag-proxy rollout restart deploy/thecrag-proxy`
- DNS (Cloudflare, zona `adrianlzt.com`): `thecrag` registro A a `79.76.123.62` **sin proxy de Cloudflare** (el
  certificado lo saca Traefik con TLS-ALPN). `thecrag.k8s` ya va por el comodín `*.k8s`.
