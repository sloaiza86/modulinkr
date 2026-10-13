#!/usr/bin/env python3
"""ModuLinkr, consumidor cloud: del broker MQTT a PostgreSQL.

El tramo final del camino del dato (fase 3): se suscribe al broker y
persiste en la base de datos según db-schema.md. Un solo formato de
entrada para las cuatro rutas de entrega (batch-format.md v3.0):

  modulinkr/v1/+/telemetry   mensajes {schema_version, samples[], debug?}
                             del gateway (publisher 255) y los supernodos
  modulinkr/v1/+/register    catálogos retenidos (alta zero-touch)

Diseño:
  - Sesión MQTT persistente (clean_session=False, client_id fijo): si el
    consumidor se reinicia, el broker retiene lo publicado con QoS 1
    entre tanto y lo entrega al volver. Los registers, además, son
    retained: llegan al suscribirse aunque sean anteriores al arranque.
  - Transacción por mensaje; deduplicación por el índice único
    (origin, ts, seq) de la base (ON CONFLICT DO NOTHING).
  - Los datos rechazados se contabilizan y confirman. Un fallo de
    infraestructura revierte la transacción y conserva el mensaje sin
    confirmar. La reconexión solicita su reentrega en la sesión persistente.

Config por variables de entorno (/etc/modulinkr/consumer.env):
  MODULINKR_MQTT_HOST         host del broker (obligatorio)
  MODULINKR_MQTT_PORT         (default 8883)
  MODULINKR_MQTT_USER         usuario MQTT
  MODULINKR_MQTT_PASS         clave MQTT
  MODULINKR_MQTT_TLS          (default 1)
  MODULINKR_MQTT_CAFILE       CA del broker; vacío = CAs del sistema
  MODULINKR_MQTT_TLS_INSECURE (default 0) 1 = no valida hostname (banco)
  MODULINKR_DB_*              ver db.py
  MODULINKR_STATS_S           (default 60) periodo del STATS en el log
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import ssl
import time

import paho.mqtt.client as mqtt

from db import Db
from ingest import ingest_message
from catalog import process_register
from health import process_health
from consumer_status import ConsumerStatus

LOG = logging.getLogger("modulinkr.consumer")

TOPIC_RE = re.compile(r"^modulinkr/v1/(\d+)/(telemetry|register|health)$")

# Contador de descarte por tipo de topic, para que un payload ilegible no se
# contabilice siempre como register.
_BAD_KEY = {"telemetry": "msg_bad", "register": "reg_bad", "health": "hlt_bad"}

STATS_KEYS = ("msg_tel", "msg_bad", "msg_test", "inserted", "dup",
              "quarantined", "sample_bad", "reg_ok", "reg_synced",
              "reg_bad", "materialized", "hlt_ok", "hlt_dup", "hlt_bad",
              "infra_err")


class Consumer:
    def __init__(self):
        self.host      = os.environ.get("MODULINKR_MQTT_HOST", "")
        self.port      = int(os.environ.get("MODULINKR_MQTT_PORT", "8883"))
        self.user      = os.environ.get("MODULINKR_MQTT_USER", "")
        self.password  = os.environ.get("MODULINKR_MQTT_PASS", "")
        self.tls       = os.environ.get("MODULINKR_MQTT_TLS", "1") == "1"
        self.cafile    = os.environ.get("MODULINKR_MQTT_CAFILE", "")
        self.tls_insec = os.environ.get("MODULINKR_MQTT_TLS_INSECURE", "0") == "1"
        self.stats_s   = float(os.environ.get("MODULINKR_STATS_S", "60"))

        self.db      = Db()
        self.stats   = {k: 0 for k in STATS_KEYS}
        self.running = True
        self.status = ConsumerStatus()
        self._retry_at = 0

        # clean_session=False + client_id fijo: el broker guarda la
        # suscripción y los QoS 1 pendientes entre reinicios del servicio.
        try:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id="modulinkr-consumer", clean_session=False,
                protocol=mqtt.MQTTv311)
        except (AttributeError, TypeError):
            self.client = mqtt.Client(
                client_id="modulinkr-consumer", clean_session=False,
                protocol=mqtt.MQTTv311)

        if self.user:
            self.client.username_pw_set(self.user, self.password)
        if self.tls:
            self.client.tls_set(
                ca_certs=self.cafile or None,
                tls_version=ssl.PROTOCOL_TLS_CLIENT)
            if self.tls_insec:
                self.client.tls_insecure_set(True)

        self.client.on_connect    = self._on_connect
        self.client.manual_ack_set(True)
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    # ----- Callbacks MQTT -----

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc != 0:
            LOG.error("event=mqtt.connection_rejected rc=%s", rc)
            return
        LOG.info("event=mqtt.connected host=%s port=%d session_present=%s",
                 self.host, self.port, bool(flags.get("session present")))
        self.status.update(mqtt_connected=True)
        # La suscripción se (re)emite en cada conexión: con sesión
        # persistente es redundante pero inocua, y cubre el primer
        # arranque y los brokers reiniciados sin estado.
        client.subscribe([("modulinkr/v1/+/telemetry", 1),
                          ("modulinkr/v1/+/register", 1),
                          ("modulinkr/v1/+/health", 1)])

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.status.update(mqtt_connected=False)
        if rc != 0:
            LOG.warning("event=mqtt.disconnected rc=%s retry=true", rc)

    def _on_message(self, client, userdata, msg) -> None:
        m = TOPIC_RE.match(msg.topic)
        if m is None:
            LOG.warning("event=mqtt.topic_rejected topic=%s", msg.topic)
            client.ack(msg.mid, msg.qos)
            return
        publisher, kind = int(m.group(1)), m.group(2)

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except (ValueError, UnicodeDecodeError) as e:
            self.stats[_BAD_KEY[kind]] += 1
            LOG.warning("event=payload.parse_failed topic=%s error=%s", msg.topic, e)
            client.ack(msg.mid, msg.qos)
            return

        before = {key: self.stats[key] for key in ('inserted', 'quarantined', 'dup',
                                                  'reg_ok', 'reg_synced', 'materialized', 'hlt_ok', 'hlt_dup')}
        try:
            if kind == "telemetry":
                self.stats["msg_tel"] += 1
                ingest_message(self.db, publisher, payload, self.stats)
            elif kind == "health":
                process_health(self.db, publisher, payload, self.stats)
            else:
                process_register(self.db, publisher, payload, self.stats)
            if any(self.stats[key] != value for key, value in before.items()):
                self.status.update(write_error=False, last_saved=time.time())
            client.ack(msg.mid, msg.qos)
        except Exception:                            # noqa: BLE001
            self.stats["infra_err"] += 1
            for key, value in before.items():
                self.stats[key] = value
            self.db.rollback()
            self.status.failed()
            LOG.exception("event=message.processing_failed topic=%s", msg.topic)
            # Sin confirmación, la sesión persistente reentrega al reconectar.
            self._retry_at = time.monotonic() + 5
            client.disconnect()

    # ----- Ciclo de vida -----

    def run(self) -> int:
        if not self.host:
            LOG.error("event=config.missing key=MODULINKR_MQTT_HOST")
            return 2

        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)

        LOG.info("event=mqtt.connecting host=%s port=%d transport=%s",
                 self.host, self.port,
                 "tcp" if not self.tls else
                 ("tls-insecure" if self.tls_insec else "tls"))
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

        last_stats = time.monotonic()
        while self.running:
            time.sleep(0.5)
            now = time.monotonic()
            if self._retry_at and now >= self._retry_at:
                self.client.loop_stop()
                try:
                    self.client.reconnect()
                    self.client.loop_start()
                    self._retry_at = 0
                except Exception:
                    self._retry_at = now + 5
                    LOG.warning('event=mqtt.redelivery_reconnect_failed')
            if now - last_stats >= self.stats_s:
                last_stats = now
                self.report_stats()

        LOG.info("event=service.stopping reason=signal")
        self.client.loop_stop()
        try:
            self.client.disconnect()
        except Exception:                            # noqa: BLE001
            pass
        self.report_stats()
        return 0

    def _on_sigterm(self, signum, frame) -> None:
        self.running = False

    def report_stats(self) -> None:
        self.status.update()
        s = self.stats
        LOG.info("STATS msgs=%d bad=%d test=%d | ins=%d dup=%d quar=%d "
                 "sbad=%d | reg ok=%d sync=%d bad=%d mat=%d | "
                 "health ok=%d dup=%d bad=%d | infra=%d",
                 s["msg_tel"], s["msg_bad"], s["msg_test"], s["inserted"],
                 s["dup"], s["quarantined"], s["sample_bad"], s["reg_ok"],
                 s["reg_synced"], s["reg_bad"], s["materialized"],
                 s["hlt_ok"], s["hlt_dup"], s["hlt_bad"],
                 s["infra_err"])


def main() -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=os.environ.get("MODULINKR_LOG_LEVEL", "INFO"),
        format="%(asctime)sZ %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S")
    return Consumer().run()


if __name__ == "__main__":
    raise SystemExit(main())
