# ModuLinkr, servicio del gateway (lado Pi)

El cerebro del gateway. Corre sobre el Raspberry Pi Zero 2W y habla con el
Heltec (radio pura) por USB serial. Desde el 5 de julio de 2026, el Pi
genera el ACK y el BEACON que antes generaba el Heltec de forma autónoma
(ver `firmware/shared/protocol/frame-format.md` §12).

## Piezas

| Archivo | Función |
| --- | --- |
| `protocol.py` | Librería del protocolo v2.0: constantes, CRC16, `parse_frame`, `build_ack`, `build_beacon`. Sin dependencia de hardware. Fuente canónica para el lado Pi. |
| `buffer.py` | Buffer SQLite pequeño de reenvío. Clave primaria `(origin_id, seq)` para deduplicación e idempotencia, cota FIFO. |
| `gateway_service.py` | Servicio principal: lee del Heltec, valida, acepta en buffer, emite ACK y BEACON, lleva el contador `seq` descendente. |
| `systemd/modulinkr-gateway.service` | Unidad systemd con reinicio automático. |
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

## Despliegue al Pi

Copiar los archivos al Pi (vía `scp` a `practica@SuperNodo1.local:~/pi-service/`)
y usar el venv existente `~/modbus-test` (ya tiene pyserial).

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
