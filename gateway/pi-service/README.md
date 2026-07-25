# ModuLinkr, servicio del gateway (lado Pi)

El cerebro del gateway. Corre sobre el Raspberry Pi Zero 2W y habla con el
Heltec (radio pura) por USB serial. Desde el 5 de julio de 2026, el Pi
genera el ACK y el BEACON que antes generaba el Heltec de forma autónoma
(ver `firmware/shared/protocol/frame-format.md` §12).

## Piezas

| Archivo | Función |
| --- | --- |
| `protocol.py` | Librería del protocolo v3.1: constantes, CRC16, `parse_frame`, los `build_*`, la seguridad v2.2 (AES-CCM, nonce, AAD) y `toa_ms` (Time-on-Air para el duty cycle propio). Sin dependencia de hardware. Fuente canónica para el lado Pi. |
| `buffer.py` | Buffer SQLite pequeño de reenvío. Clave primaria `(origin, ts, seq)` para deduplicación e idempotencia, cota FIFO. Expone `fetch_pending` / `mark_published` (telemetría), sus equivalentes de catálogo, `node_status` (estado de red para el visor), `node_airtime` (reportes `tx_ms` del duty cycle v3.1, totalizados por deltas en `airtime_duty`) y `gateway_status` (fila única con el latido del servicio para el visor: `lora_link`, `mqtt_enabled`, `mqtt_connected` y `t_updated`). |
| `mqtt_publisher.py` | Cliente Paho que republica el buffer al broker MQTT cloud: telemetría en el mensaje unificado de `batch-format.md` a `modulinkr/v1/255/telemetry` y catálogo a `modulinkr/v1/{origin}/register` (retenido). Marca `published=1` solo tras el PUBACK. |
| `gateway_service.py` | Servicio principal: lee del Heltec, valida, acepta en buffer, emite ACK y BEACON, lleva el contador `seq` descendente, y drena el buffer a cloud cada `MODULINKR_MQTT_DRAIN_S`. Desde v3.1 registra los HEARTBEAT con `tx_ms` (sin confirmarlos) y contabiliza su propio aire en `_tx` con `toa_ms`, reportándose con la cadencia del beacon (duty cycle por transmisor, EN 300 220-1). Escribe el latido de estado (`gateway_status`) cada `MODULINKR_HEARTBEAT_S`; ante una desconexión del Heltec marca `lora_link=0` en el acto, cierra el puerto y reintenta abrirlo sin morir, en vez de caer y dejar que systemd lo recicle. |
| `systemd/modulinkr-gateway.service` | Unidad systemd con reinicio automático. |
| `flash_heltec.sh` | Flashea el firmware de la radio Heltec (ESP32-S3) por USB con el esptool del venv: imagen única desde `0x0`, auto-reset por el CP2102 (sin botones), servicio parado y rearrancado alrededor. El binario `heltec-radio.bin` lo genera `heltec-radio/make_dist.sh` en el Mac (merge de los artefactos de PlatformIO) y viaja con el mismo scp del pi-service; el instalador lo ofrece como paso opcional para la instalación fresca. |
| `set_mqtt.sh` | Acción privilegiada de la página "Configurar MQTT" del visor. Reescribe las claves `MODULINKR_MQTT_*` de `gateway.env` con los pares `KEY=VALUE` que llegan por stdin (lista blanca; los secretos no van por argumentos) y reinicia el servicio del gateway. Solo toca las claves recibidas (una omitida conserva su valor). |
| `set_db.sh` | Acción privilegiada de la página "Configurar base de datos" del visor. Reescribe las claves `MODULINKR_PG_*` de `web.env` por stdin, sin reiniciar el visor (el módulo de datos las relee en caliente). |
| `flash_nodo.sh` | Acción privilegiada del asistente de configuración de nodo. Flashea `nodo.bin` (firmware del Atom, ESP32) por USB en el puerto indicado como argumento, excluyendo el del gateway. A 115200 baud (el puente USB del Atom no sostiene el cambio a 460800 sobre el Pi). El binario `nodo.bin` lo genera `nodo/make_dist.sh` en el Mac y viaja con el scp del pi-service; junto a él, `nodo.bin.version` lleva la versión del firmware. |
| `get_net.sh` | Lectura privilegiada (solo lectura) de los parámetros de red de `gateway.env` que todo nodo debe compartir para unirse (Network ID, TTL, SF, ancho de banda, seguridad y el broker MQTT, incluida la clave). El asistente los usa para bloquear esos campos del formulario a los valores reales. Extrae con `sed` para tolerar claves ausentes. |
| `heltec_rx_parser.py` | Visor de depuración anterior (solo lectura). Se conserva como herramienta; la lógica productiva vive en `gateway_service.py`. |

## Rol del gateway y semántica del ACK

El ACK con `status = OK` significa que **el Pi aceptó el dato en su buffer
local** (custodia), no solo que el radio lo oyó. Si este servicio se cae,
deja de emitir ACK y BEACON: los nodos pierden al gateway como padre (sin
beacon) y agotan reintentos (sin ACK), y escalan al respaldo NB-IoT. Por
eso el servicio corre bajo systemd con `Restart=always`.

## Enlace serial con el Heltec (frame-format.md §12)

- Heltec a Pi: `[rx] #N len=L rssi=X snr=Y hex=...` por cada trama del aire.
- Pi a Heltec: `TX <hex>` por cada trama a transmitir (ACK, BEACON).
- Pi a Heltec: `OLED <ssid>\t<red>\t<ip>\t<en_linea>\t<fuera_de_linea>` con el estado para la pantalla del Heltec, cada `MODULINKR_OLED_S` (`frame-format.md` §12.5).

## Configuración (variables de entorno, con defaults)

| Variable | Default | Descripción |
| --- | --- | --- |
| `MODULINKR_PORT` | `/dev/ttyUSB0` | Puerto del Heltec |
| `MODULINKR_BAUD` | `115200` | Baudios |
| `MODULINKR_NETWORK_ID` | `1` | Debe coincidir con los nodos |
| `MODULINKR_MAX_TTL` | `4` | TTL inicial de ACK y BEACON |
| `MODULINKR_BEACON_S` | `30` | Periodo del beacon en segundos |
| `MODULINKR_DB` | `/home/practica/modulinkr_buffer.db` | Ruta del buffer SQLite |
| `MODULINKR_BUFFER_MAX` | `1000` | Cota de entradas del buffer |
| `MODULINKR_SF` | `7` | SF del despliegue, para el ToA del aire propio (v3.1) |
| `MODULINKR_BW_KHZ` | `125` | BW del despliegue, ídem |
| `MODULINKR_HEARTBEAT_S` | `3` | Periodo del latido de estado hacia el visor, en segundos |
| `MODULINKR_OLED_S` | `5` | Periodo del empuje de estado a la pantalla OLED del Heltec, en segundos |
| `MODULINKR_ONLINE_S` | `60` | Umbral "en línea" del conteo de nodos de la pantalla (igual que el `MODULINKR_WEB_ONLINE_S` del visor) |
| `MODULINKR_NETWORK_NAME` | (sin default) | Nombre de la red ModuLinkr que muestra la pantalla. Vacío usa `net <network_id>` |
| `MODULINKR_SEC_ENABLED` | `0` | Seguridad v2.2 de la interfaz aire (`frame-format.md` §14) |
| `MODULINKR_SEC_KEY` | (sin default) | Clave de red, 32 caracteres hex. Obligatoria con `SEC_ENABLED=1`. **Debe coincidir** con `security.key` del config de todos los nodos |
| `MODULINKR_MQTT_HOST` | (vacío) | Host del broker cloud. Vacío deja el MQTT desactivado y la telemetría se acumula en el buffer local |
| `MODULINKR_MQTT_PORT` | `8883` | Puerto del broker |
| `MODULINKR_MQTT_USER` | (vacío) | Usuario MQTT |
| `MODULINKR_MQTT_PASS` | (vacío) | Clave MQTT. La pregunta el instalador al ejecutarse, no se escribe en el código |
| `MODULINKR_MQTT_TLS` | `1` | `1` TLS, `0` en claro (solo banco o broker en localhost) |
| `MODULINKR_MQTT_CAFILE` | (vacío) | Cert de la CA (RSA) que firma el broker. Vacío usa las CAs del sistema |
| `MODULINKR_MQTT_CERTFILE` | (vacío) | Cert de cliente para mTLS (opcional) |
| `MODULINKR_MQTT_KEYFILE` | (vacío) | Clave del cert de cliente (opcional) |
| `MODULINKR_MQTT_TLS_INSECURE` | `0` | `1` no valida el hostname del cert (solo banco con cert autofirmado) |
| `MODULINKR_MQTT_DRAIN_S` | `2.0` | Periodo del drenado del buffer a cloud, en segundos |
| `MODULINKR_MQTT_DRAIN_MAX` | `50` | Muestras de telemetría por vuelta de drenado (van en un solo mensaje) |
| `MODULINKR_MQTT_PUB_TIMEOUT` | `5.0` | Espera del PUBACK antes de dejar la muestra pendiente, en segundos |
| `MODULINKR_MQTT_DEBUG` | `1` | `1` = el mensaje de telemetría lleva el sobre `debug` (`batch-format.md` §5) |

## Publicación al broker cloud (MQTT)

`mqtt_publisher.py` drena el buffer al broker cloud (Mosquitto self-hosted con
TLS RSA, `Red V4.md` §Infraestructura cloud). Desde v3.0 la telemetría sale en
el **mensaje unificado** de `batch-format.md`: un publish por vuelta de drenado
a `modulinkr/v1/255/telemetry` (QoS 1, sin retener; 255 = publisher gateway),
con payload `{"schema_version","samples":[{"origin","seq","ts","v"},...]}` más
el sobre `debug` opcional (`MODULINKR_MQTT_DEBUG`). Es el mismo formato que
publican los supernodos por NB-IoT: el consumidor cloud tiene un solo parser.
El catálogo de cada nodo se republica a `modulinkr/v1/{origin}/register`
(QoS 1, retenido), en el formato de `batch-format.md` §10.2, para que el
consumidor cloud tenga la leyenda de las lecturas antes que los datos.

La marca `published=1` solo se pone tras el PUBACK del broker: un corte de
Internet o un reinicio del servicio deja la muestra pendiente y se reintenta.
El consumidor cloud deduplica los reenvíos por `(origin, ts, seq)`, de modo que
una muestra llegada también por NB-IoT no se guarda dos veces.

## Seguridad de la interfaz aire (v2.2)

Con `MODULINKR_SEC_ENABLED=1` el servicio verifica el MIC y descifra cada
trama entrante (AES-CCM, `frame-format.md` §14) y cifra y firma todo lo que
emite (ACK, BEACON, WELCOME). Es un ajuste de **toda la red**: ON aquí y
OFF en un nodo (o claves distintas) significa que las tramas de ese nodo
fallan el MIC y se descartan (contador `micfail` en STATS). Requiere el
paquete `cryptography` en el venv:

```bash
source ~/modbus-test/bin/activate
pip install cryptography
```

## Despliegue al Pi

Método recomendado: el instalador (`installer/`). Copiar la carpeta `pi-service`
al Pi (vía `scp` a `practica@SuperNodo1.local:~/pi-service/`) y correr:

```bash
cd ~/pi-service/installer
sudo ./install.sh
```

El instalador crea el venv dedicado, instala las dependencias, pregunta la
config (serie, red, seguridad y broker MQTT, con las contraseñas confirmadas),
guarda los secretos en `/etc/modulinkr/gateway.env` y deja el servicio bajo
systemd. Ver `installer/README.md`. Los pasos manuales de abajo son la
alternativa para depurar o para instalaciones a medida.

Ejecución manual para pruebas:

```bash
source ~/modbus-test/bin/activate
python3 ~/pi-service/gateway_service.py       # -v para modo debug
```

Instalación como servicio systemd:

```bash
sudo cp ~/pi-service/systemd/modulinkr-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now modulinkr-gateway
journalctl -u modulinkr-gateway -f            # ver logs en vivo
```

## Prueba de la caída del Pi (validación clave del cambio)

Con la red en marcha, detener el servicio y comprobar que los nodos escalan
a NB-IoT al perder ACK y beacon:

```bash
sudo systemctl stop modulinkr-gateway
# observar en el monitor del nodo: pierde padre, agota reintentos, activa NB-IoT
sudo systemctl start modulinkr-gateway
# el nodo recupera al gateway como padre y vuelve a la ruta LoRa
```
