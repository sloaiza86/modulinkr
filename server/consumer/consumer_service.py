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
  - Un error de datos nunca tumba el servicio (log y contador); un error
    de infraestructura (BBDD caída) hace rollback y deja que el mensaje
    se pierda de esta entrega: systemd y la sesión persistente sostienen
    la operación, y las rutas de reintento del sistema (batch NB-IoT,
    buffer del gateway) reentregan lo importante.

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

LOG = logging.getLogger("modulinkr.consumer")

TOPIC_RE = re.compile(r"^modulinkr/v1/(\d+)/(telemetry|register)$")

STATS_KEYS = ("msg_tel", "msg_bad", "msg_test", "inserted", "dup",
              "quarantined", "sample_bad", "reg_ok", "reg_synced",
              "reg_bad", "materialized", "infra_err")


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
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    # ----- Callbacks MQTT -----

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc != 0:
            LOG.error("MQTT rechazado por el broker (rc=%s)", rc)
            return
        LOG.info("MQTT conectado a %s:%d%s", self.host, self.port,
                 " (sesion previa presente)" if flags.get("session present") else "")
        # La suscripción se (re)emite en cada conexión: con sesión
        # persistente es redundante pero inocua, y cubre el primer
        # arranque y los brokers reiniciados sin estado.
        client.subscribe([("modulinkr/v1/+/telemetry", 1),
                          ("modulinkr/v1/+/register", 1)])

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            LOG.warning("MQTT desconectado (rc=%s), reintentando", rc)

    def _on_message(self, client, userdata, msg) -> None:
        m = TOPIC_RE.match(msg.topic)
        if m is None:
            LOG.warning("topic inesperado: %s", msg.topic)
            return
        publisher, kind = int(m.group(1)), m.group(2)

        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("el payload no es un objeto JSON")
        except (ValueError, UnicodeDecodeError) as e:
            self.stats["msg_bad" if kind == "telemetry" else "reg_bad"] += 1
            LOG.warning("payload no parseable en %s: %s", msg.topic, e)
            return

        try:
            if kind == "telemetry":
                self.stats["msg_tel"] += 1
                ingest_message(self.db, publisher, payload, self.stats)
            else:
                process_register(self.db, publisher, payload, self.stats)
        except Exception:                            # noqa: BLE001
            # Infraestructura (BBDD caída, etc.): rollback y a seguir. El
            # detalle queda en el log; el dato lo reentrega el sistema.
            self.stats["infra_err"] += 1
            self.db.rollback()
            LOG.exception("fallo procesando %s", msg.topic)

    # ----- Ciclo de vida -----

    def run(self) -> int:
        if not self.host:
            LOG.error("falta MODULINKR_MQTT_HOST; nada que consumir")
            return 2

        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)

        LOG.info("conectando a %s:%d (%s)", self.host, self.port,
                 "en claro" if not self.tls else
                 ("TLS insecure" if self.tls_insec else "TLS"))
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

        last_stats = time.monotonic()
        while self.running:
            time.sleep(0.5)
            now = time.monotonic()
            if now - last_stats >= self.stats_s:
                last_stats = now
                self.report_stats()

        LOG.info("parando (senal recibida)")
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
        s = self.stats
        LOG.info("STATS msgs=%d bad=%d test=%d | ins=%d dup=%d quar=%d "
                 "sbad=%d | reg ok=%d sync=%d bad=%d mat=%d | infra=%d",
                 s["msg_tel"], s["msg_bad"], s["msg_test"], s["inserted"],
                 s["dup"], s["quarantined"], s["sample_bad"], s["reg_ok"],
                 s["reg_synced"], s["reg_bad"], s["materialized"],
                 s["infra_err"])


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("MODULINKR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    return Consumer().run()


if __name__ == "__main__":
    raise SystemExit(main())
