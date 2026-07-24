# ModuLinkr, visor web del gateway (diseño)

Documento de diseño del servidor web local del Pi. **Estado: fases 1 a 4 implementadas el 16-jul-2026** (tabla `node_status` en el pi-service; esqueleto FastAPI con basic auth y módulos de red y topología; módulo de datos contra el PostgreSQL de la VM con series agregadas y export CSV, `dataapi.py`; instalador integrado en el Gateway Installer, `installer/lib/web.sh`, servicio `modulinkr-web` con env en `/etc/modulinkr/web.env`); pendiente la fase 5 (comandos, §5). La fase 3.1 añadió el selector escalable (filtro, modos por nodo y por medida, vistas guardadas) y los ejes automáticos por unidad con paneles apilados. Con el schema v3.1, la vista de red muestra además el duty cycle de la última hora por transmisor (semáforo sobre el límite del 10 % del g3, y el del gateway en su tarjeta), calculado por deltas de los reportes `tx_ms` de la tabla `node_airtime` (`frame-format.md` §6).

**Rediseño de interfaz (19-jul-2026, validado en el Pi)**: tema claro con sidebar contraible (Red, Topología, Datos, Configuración) al estilo de los paneles domóticos, navegación por hash y estado del menú recordado en el navegador. La vista de red pasa de tabla a tarjetas por nodo: nombre, estado en tres niveles ("en línea", "en línea · sin datos" en ámbar cuando el nodo responde por LoRa pero su sensor no entrega con umbral 5 veces el de conexión, y "sin señal"), los dos tiempos rotulados ("última vez visto hace", "última medida recibida hace") y una fila por medida con icono, miniatura de la última hora y valor con unidad al borde derecho. El detalle técnico (RSSI, SNR, padre, hop, duty cycle, firmware) vive en un panel lateral que se abre al pulsar la tarjeta; pulsar una medida abre un modal con el histórico de los últimos 5 días servido por la API de datos (zoom temporal con la rueda, barra de desplazamiento, eje de magnitud autoajustado y cambio de día destacado en el eje). El endpoint `GET /api/red/ultimos` sirve los últimos valores y la serie de la última hora desde el `reads_json` del buffer local. La autenticación pasa de basic auth a página de login propia con cookie de sesión firmada (HMAC SHA-256, `MODULINKR_WEB_SECRET` autogenerada por el instalador); la cookie es stateless y sobrevive reinicios del servicio. Identidad visual del logo del proyecto (`static/img`): favicon, logo completo en el login, logo de texto en la sidebar, y paleta derivada de sus colores en `:root` de `style.css` (cambiar el acento es cambiar `--accent`). Los iconos son SVG de trazo propios: los del menú en `index.html`, los de tarjetas y sensores en el diccionario `ICONO` de `app.js` (elección por palabra clave de la medida en `iconoMedida`). Las unidades crudas del catálogo se muestran en forma tipográfica (`C` como `°C`, mapa `unidad()` en `app.js`).

Arranque manual en banco (sin instalador todavía): `./get_vendor.sh` una vez con Internet (descarga vis-network y ECharts a `static/vendor/`), venv con `fastapi`, `uvicorn` y `psycopg2-binary`, y:

```
MODULINKR_DB=<ruta del buffer> \
MODULINKR_PG_HOST=<dominio de la VM> MODULINKR_PG_PASSWORD=<clave de modulinkr_ro> \
uvicorn web_service:app --host 0.0.0.0 --port 8080
```

Sin `MODULINKR_WEB_USER` el visor arranca sin autenticación y lo avisa en el log; sin `MODULINKR_PG_HOST` solo degrada el módulo de datos (503), el resto opera. El rol `modulinkr_ro` y el listener remoto los provisiona el componente `database` del instalador del servidor (`db_enable_remote_ro`); requiere además abrir 5432/tcp en el firewall de la nube. El cliente conecta con `sslmode=require`: canal cifrado, identidad del servidor no verificada (la protección es la contraseña; mejora futura: `verify-full` con el certificado del dominio).

## 1. Propósito y alcance

Interfaz web servida desde el Raspberry Pi del gateway con cuatro funciones en fase 1 y espacio para crecer:

1. **Datos**: gráficos de las medidas guardadas en la base cloud y export CSV, con selección de nodos, medidas y rango temporal.
2. **Estado de la red**: qué nodos están conectados, cuáles se vieron alguna vez y cuándo fue la última vez, y por qué ruta entregan (LoRa o NB-IoT).
3. **Comandos de escritura**: pospuesto. Queda como módulo stub hasta que el firmware de comandos madure (recepción en supernodo incompleta; downlink LoRa a nodos sin NB-IoT sin implementar, `frame-format.md` §11).
4. **Topología**: mapa del árbol mesh (gateway raíz, arista hijo a padre), al estilo del mapa de red de Zigbee2MQTT.

## 2. Decisiones de arquitectura (16-jul-2026)

- **Corre en el Pi** (`modulinkr-web`, servicio systemd separado del gateway). El Pi es el dueño de la verdad viva de la red; las funciones 2 y 4 operan 100 % en local y sobreviven sin Internet. La función 1 consulta la base remota y degrada con aviso si no hay conexión.
- **Stack**: FastAPI + uvicorn (Python, coherente con el resto del proyecto), frontend estático servido por el propio backend, sin build ni Node: ECharts para gráficos, vis-network para el mapa. Presupuesto de memoria ~40-60 MB (los gráficos los renderiza el navegador del cliente; el Pi solo sirve JSON y estáticos).
- **Modularidad**: un router de API por función (`/api/datos`, `/api/red`, `/api/topologia`; futuro `/api/comandos`), cada uno con su vista. Añadir una función es añadir un módulo, sin tocar los demás.
- **Acceso al histórico**: PostgreSQL de la VM expuesto en 5432 con TLS y un rol **solo lectura** dedicado (`modulinkr_ro`, únicamente `SELECT` sobre la base de telemetría; `pg_hba` restringido a ese rol y base). Lo provisiona el instalador del servidor.
- **Autenticación**: página de login propia con cookie de sesión firmada (HMAC SHA-256 con `MODULINKR_WEB_SECRET`; usuario y contraseña preguntados en la instalación, la clave de firma autogenerada). Sustituye al basic auth de la primera iteración: misma protección, sin el diálogo del navegador y con logout. Sobre HTTP en la LAN (`http://gateway.local:8080`); sin TLS local en fase 1, se revisita si la web se expone fuera de la LAN.

## 3. Fuentes de datos

| Función | Fuente | Notas |
| --- | --- | --- |
| Datos (gráficos, CSV) | PostgreSQL de la VM (`nodes`, `channels`, `samples`, `sample_values`) | Selector de nodos y medidas desde el catálogo zero-touch. Consultas de `db-schema.md` §5. Rangos largos con agregación en servidor (`date_trunc`, promedio por bucket) para no arrastrar la serie cruda hasta el navegador. CSV en streaming con la misma consulta. |
| Estado de red | Tabla nueva `node_status` en el `buffer.db` del Pi + cloud cuando hay Internet | Local: última trama oída por LoRa, tipo, RSSI, SNR, padre, hop (persistente a reinicios). Cloud: `max(received_at)` por origen y `source`, que cubre lo que el Pi no ve (entregas NB-IoT). La vista mezcla ambas y marca la ruta. "Conectado" = visto hace menos de N intervalos de muestreo. |
| Topología | `node_status` (padre y hop por nodo) | El dato viaja en los ecos de BEACON que el Heltec oye de refilón (`parent_id`, `hop_count`, §7.2) y que hoy el servicio descarta como overheard. Cosecharlos es el único cambio al pi-service. |
| Comandos (futuro) | Publicación MQTT a `modulinkr/v1/{node}/cmd` (`commands-format.md`) | Stub en fase 1. |

## 4. Cambios al pi-service (únicos, pequeños)

1. Tabla `node_status` en `buffer.db`: una fila por nodo (`origin`, `last_seen`, `last_frame_type`, `rssi`, `snr`, `parent_id`, `hop_count`). La actualiza `gateway_service.py` con cada trama válida oída (también las overheard).
2. Cosecha de los ecos de BEACON: hoy se descartan; pasan a alimentar `parent_id`/`hop_count` de `node_status`.

La web abre `buffer.db` en modo solo lectura; no comparte proceso ni sockets con el gateway.

## 5. Fases de implementación

1. `node_status` y cosecha de topología en el pi-service (validable sola, sin web).
2. Esqueleto FastAPI con basic auth + módulos de red y topología (100 % local).
3. Módulo de datos contra el Postgres remoto (rol `modulinkr_ro` en el instalador del servidor) con export CSV.
4. Instalador del visor (patrón del pi-service: venv, systemd, credenciales preguntadas, idempotente).
5. Módulo de comandos, cuando el firmware lo soporte.

## 6. Configuración desde el visor (24-jul-2026)

La vista Configuración adopta el patrón de ajustes de los paneles domóticos (items con icono, subtítulo y chevron; subrutas con volver) y aloja dos páginas operativas:

**Cargar JSON vía USB** (`configapi.py`, `/api/config`): comisionamiento de un nodo Atom conectado por USB al Pi con el protocolo `CFG.*` del firmware (detección por sondeo de `CFG.HELLO`, lectura, carga con sha256 y borrado). El veredicto de validación es el del nodo; tras cargar o borrar, el visor re-detecta para confirmar el reinicio. El puerto del Heltec queda excluido de la búsqueda (`MODULINKR_GATEWAY_PORT` en `web.env`) y las operaciones serie van bajo un lock global.

**Configurar radio LoRa** (`radioapi.py`, `/api/radio`): estado del servicio y del puerto, cambio del puerto del Heltec (`set_lora_port.sh`: `gateway.env`, `web.env` y reinicio del servicio) y flasheo de `heltec-radio.bin` (`flash_heltec.sh`). Ambas acciones corren con `sudo -n` bajo la regla acotada que el instalador deja en `/etc/sudoers.d/modulinkr-web`, limitada a esos dos scripts.

La tarjeta del gateway en la vista Red muestra dos enlaces independientes desde el latido de estado del servicio (`gateway_status`): un chip LoRa (radio del Heltec) y un chip MQTT (conexión al broker cloud). El servicio refresca el latido cada `MODULINKR_HEARTBEAT_S`, y ante una desconexión del Heltec marca `lora_link=0` en el acto: el chip LoRa pasa a "sin señal" en el siguiente sondeo de la web (unos segundos), sin esperar el hueco del auto-reporte de aire (antes hasta `MODULINKR_WEB_ONLINE_S`). El chip MQTT distingue conectado, sin conexión y no configurado, y es ortogonal al de LoRa (la nube puede estar arriba con la radio caída y viceversa). `netstatus.gateway_link_state` da el servicio por caído si el latido no se refresca dentro de `MODULINKR_WEB_HEARTBEAT_S` (default 15 s); un buffer anterior a la tabla `gateway_status` cae al veredicto antiguo del auto-reporte de aire con un solo chip.

## 7. Descartes razonados

- **Grafana**: cubre solo la función 1, pesa 150-300 MB (inviable en el Zero 2W) y obligaría a construir igualmente el resto. Queda como opción futura en la VM apuntando al mismo Postgres si algún día hace falta análisis avanzado.
- **Home Assistant**: el patrón modular se copia; la plataforma no (dimensionada para cientos de integraciones domóticas, ajena a este dominio).
- **Web en la VM**: era la opción recomendada por cercanía al dato histórico, descartada en favor del Pi para tener el estado vivo de la red en campo sin depender de Internet.
