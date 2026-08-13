# ModuLinkr, consumidor cloud

Servicio Python que cierra la fase 3 del camino del dato: se suscribe al broker MQTT y persiste la telemetría en PostgreSQL según [`db-schema.md`](../../shared/protocol/db-schema.md).

El servicio está desplegado y su ingestión quedó validada en la VM según el registro de la bitácora.

| Archivo | Qué hace |
| --- | --- |
| `consumer_service.py` | Bucle principal: conexión MQTT (sesión persistente, QoS 1), dispatch por topic, STATS periódico al log. |
| `ingest.py` | Validación del mensaje unificado ([`batch-format.md`](../../shared/protocol/batch-format.md) §8) e ingesta con deduplicación y cuarentena (db-schema.md §4). |
| `catalog.py` | Registers (alta zero-touch, db-schema.md §3): alta de nodos, versionado de canales (serie nueva siempre) y materialización de la cuarentena. Incluye el parser del catálogo binario para la variante `raw_catalog` (copiado de `gateway/pi-service/protocol.py`). |
| `db.py` | Conexión a PostgreSQL con reconexión perezosa. |
| `systemd/modulinkr-consumer.service` | Unidad systemd (usuario `modulinkr-consumer`, reinicio automático). |

## Entradas

- `modulinkr/v1/+/telemetry` (QoS 1): mensajes `{schema_version, samples[], debug?}` del gateway (publisher 255) y de los supernodos. `source` de cada muestra se deriva del publisher: 255 es `lora`, el resto `nbiot`.
- `modulinkr/v1/+/register` (QoS 1, retained): catálogos, en la variante normal (§10.2) o en custodia (`raw_catalog`, §10.4).

La sesión MQTT es persistente (`clean_session=false`, client_id fijo): un reinicio del servicio no pierde lo publicado entre tanto, y los registers retenidos repueblan el catálogo solos en el primer arranque sobre una base vacía.

## Garantías

Transacción por mensaje. Deduplicación por el índice único `(origin, ts, seq)` con `ON CONFLICT DO NOTHING`: la misma muestra llegada por LoRa y por NB-IoT se guarda una vez. Una muestra sin catálogo o con longitud que no cuadra no se rechaza: va a `quarantine` y se materializa al llegar su register. Una muestra malformada (sin `ts`, fuera de rango) se descarta con log sin afectar al resto del mensaje.

## Instalación

Componente `consumer` del instalador del servidor:

```bash
sudo ./install.sh --components consumer
```

Pregunta el broker (host del certificado TLS, puerto, usuario, contraseña) y reutiliza las credenciales de la base de `/etc/modulinkr/database.env` si existen. Deja el código en `/opt/modulinkr/consumer`, el venv con `paho-mqtt` y `psycopg2-binary`, las credenciales en `/etc/modulinkr/consumer.env` (solo root) y el servicio activo.

## Operación

```bash
systemctl status modulinkr-consumer
journalctl -u modulinkr-consumer -f
```

El STATS periódico (default 60 s) resume mensajes, inserciones, duplicados, cuarentena y registers. Salud del sistema: `SELECT origin, count(*) FROM quarantine GROUP BY origin;` distinto de vacío es una alerta (db-schema.md §4.1).
