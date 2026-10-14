# ModuLinkr, consumidor cloud

Servicio Python que cierra la fase 3 del camino del dato: se suscribe al broker MQTT y persiste la telemetría en PostgreSQL según [`db-schema.md`](../../shared/protocol/db-schema.md).

El despliegue previo y su validación de ingesta se recogen en la bitácora. La supervisión y la confirmación manual descritas a continuación requieren actualizar la VM y comprobar su funcionamiento operativo.

| Archivo | Qué hace |
| --- | --- |
| `consumer_service.py` | Bucle principal: conexión MQTT (sesión persistente, QoS 1), dispatch por topic, STATS periódico al log. |
| `identity_ingest.py` | Catálogos y muestras por despliegue, equipo, sesión y huella; deduplicación, cuarentena y conflictos. |
| `health.py` | Eventos de salud 4.0 dentro del despliegue verificado. |
| `db.py` | Conexión a PostgreSQL con reconexión perezosa. |
| `systemd/modulinkr-consumer.service` | Unidad systemd (usuario `modulinkr-consumer`, reinicio automático). |

## Entradas

Los temas operativos son `modulinkr/v2/+/+/telemetry`, `modulinkr/v2/+/+/register/#` y `modulinkr/v2/+/+/health`, QoS 1. El tema `register` sin sufijo también se admite para el catálogo vigente. Se exige sobre 4.0, despliegue coincidente y publicador válido. No se suscribe ni ingiere el espacio v1.

La sesión MQTT es persistente (`clean_session=false`, client_id fijo): un reinicio del servicio no pierde lo publicado entre tanto, y los registers retenidos repueblan el catálogo solos en el primer arranque sobre una base vacía.

## Garantías

Transacción por mensaje. Deduplicación por el índice único `(origin, device_uid, boot_id, ts, seq)` con `ON CONFLICT DO NOTHING`: la misma muestra llegada por LoRa y por NB-IoT se guarda una vez. Una muestra sin catálogo o con longitud que no cuadra no se rechaza: va a `quarantine` y se materializa al llegar su register. Una muestra malformada (sin `ts`, fuera de rango) se descarta con log sin afectar al resto del mensaje.

La recepción MQTT utiliza confirmación manual con Paho 2.1. La confirmación se envía después de procesar correctamente el mensaje. Si falla la escritura, se revierte la transacción, se registra el error fuera de PostgreSQL y se desconecta la sesión sin confirmar. El servicio intenta reconectar a partir de cinco segundos después para recibir de nuevo el mensaje pendiente. La conservación depende también de la sesión y del almacenamiento del broker; esta implementación no equivale a una confirmación extremo a extremo al nodo.

`consumer_status.py` mantiene la fecha del último guardado confirmado, el estado del último error y el contador de fallos en `/var/lib/modulinkr-consumer/status.json`, con permisos restringidos. Los rechazos de formato no se presentan como escrituras correctas. `monitor_service.py` recoge cada treinta segundos la capacidad del volumen de PostgreSQL y el estado reciente del consumidor, y actualiza una única fila de `system_health`. No depende de MQTT. Si PostgreSQL no permite actualizarla, la fila anterior caduca para el visor; no se presenta como una medición actual.

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

El STATS periódico (default 60 s) resume mensajes, inserciones, duplicados, cuarentena y registers. La cuarentena se consulta dentro del esquema del despliegue; el visor presenta el recuento y sus motivos (db-schema.md §4.1).


## Identidad física y despliegues

El código del nodo 0.0.83 utiliza la radio 4.0, catálogos por huella y muestras vinculadas a equipo y sesión. El gateway bloquea identificadores repetidos y el consumidor separa los despliegues. El contrato actual, la conservación interna del banco y el procedimiento de despliegue se describen en [`identity-safety.md`](../../shared/protocol/identity-safety.md). El banco debe utilizar nodos y relays del contrato de radio 4.0; las versiones internas previas no representan clientes publicados.
