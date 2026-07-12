#!/usr/bin/env python3
"""ModuLinkr, publicador MQTT del gateway (lado Pi).

Drena el buffer local (buffer.py) hacia el broker MQTT cloud. Es el tramo
que faltaba del camino LoRa a cloud: hasta ahora el servicio recibía la
trama, la aceptaba en custodia (published=0) y ahí se quedaba. Aquí se
publica y solo se marca published=1 tras el PUBACK del broker.

Dos flujos, ambos hacia el broker cloud (Mosquitto self-hosted, TLS con
cert RSA, Red V4.md §Infraestructura cloud):

  1. Telemetría: una muestra por publish al topic
     modulinkr/v1/{origin}/telemetry, QoS 1, retained=false. Payload:
       {"schema_version": "2.1", "origin": N, "seq": S,
        "ts": T_o_null, "v": [floats]}
     El consumidor cloud deduplica por (origin, ts, seq): la misma muestra
     llegada también por NB-IoT (batch del supernodo) es un duplicado y se
     descarta (db-schema.md §2).

  2. Catálogo: mensaje register retenido al topic
     modulinkr/v1/{origin}/register, QoS 1, retained=true, formato de
     batch-format.md §10.2. El gateway republica en nombre de cada nodo que
     se registró por LoRa, para que el consumidor tenga un solo punto de
     ingesta de catálogos. Se publica antes que la telemetría de esa vuelta
     (la leyenda antes que los datos).

Custodia y entrega al menos una vez: published pasa a 1 SOLO tras el
PUBACK. Un corte de Internet o un reinicio del servicio deja la muestra
pendiente y se reintenta; el consumidor deduplica los reenvíos.

Config por variables de entorno (el instalador de fase 1 las rellena; la
clave se pregunta al ejecutar, nunca se escribe en el código):
  MODULINKR_MQTT_HOST         host del broker cloud (vacío = MQTT off)
  MODULINKR_MQTT_PORT         (default 8883)
  MODULINKR_MQTT_USER         usuario MQTT (vacío = sin auth)
  MODULINKR_MQTT_PASS         clave MQTT
  MODULINKR_MQTT_TLS          (default 1) 1 = TLS; 0 = en claro (solo banco
                              o broker en localhost sin cifrar)
  MODULINKR_MQTT_CAFILE       cert de la CA (RSA) que firma el broker;
                              vacío = CAs del sistema
  MODULINKR_MQTT_CERTFILE     cert de cliente (opcional, mTLS)
  MODULINKR_MQTT_KEYFILE      clave del cert de cliente (opcional, mTLS)
  MODULINKR_MQTT_TLS_INSECURE (default 0) 1 = no valida el hostname del
                              cert (solo para banco con cert autofirmado)
  MODULINKR_MQTT_DRAIN_MAX    (default 50) muestras por vuelta de drenado
  MODULINKR_MQTT_PUB_TIMEOUT  (default 5.0) espera del PUBACK, segundos

Requiere `pip install paho-mqtt` en el venv del servicio.
"""

from __future__ import annotations

import json
import logging
import os
import ssl

import paho.mqtt.client as mqtt

LOG = logging.getLogger("modulinkr.mqtt")

SCHEMA_VERSION   = "2.1"
TELEMETRY_TOPIC  = "modulinkr/v1/{origin}/telemetry"
REGISTER_TOPIC   = "modulinkr/v1/{origin}/register"


class MqttPublisher:
    def __init__(self, buf):
        self.buf = buf

        self.host       = os.environ.get("MODULINKR_MQTT_HOST", "")
        self.port       = int(os.environ.get("MODULINKR_MQTT_PORT", "8883"))
        self.user       = os.environ.get("MODULINKR_MQTT_USER", "")
        self.password   = os.environ.get("MODULINKR_MQTT_PASS", "")
        self.tls        = os.environ.get("MODULINKR_MQTT_TLS", "1") == "1"
        self.cafile     = os.environ.get("MODULINKR_MQTT_CAFILE", "")
        self.certfile   = os.environ.get("MODULINKR_MQTT_CERTFILE", "")
        self.keyfile    = os.environ.get("MODULINKR_MQTT_KEYFILE", "")
        self.tls_insec  = os.environ.get("MODULINKR_MQTT_TLS_INSECURE", "0") == "1"
        self.drain_max  = int(os.environ.get("MODULINKR_MQTT_DRAIN_MAX", "50"))
        self.pub_timeout = float(os.environ.get("MODULINKR_MQTT_PUB_TIMEOUT", "5.0"))

        # Sin host no hay cloud: el servicio sigue operando (recibe LoRa,
        # ACK, beacon) y las muestras se acumulan en el buffer local.
        self.enabled   = bool(self.host)
        self.client: mqtt.Client | None = None
        self.connected = False

        # Contadores de diagnóstico (se vuelcan en el STATS del servicio).
        self.n_pub_tel = 0   # muestras de telemetría publicadas (con PUBACK)
        self.n_pub_cat = 0   # catálogos republicados (con PUBACK)

    # ----- Conexión -----

    def start(self) -> None:
        """Abre la conexión al broker en segundo plano. No bloquea: paho
        gestiona la conexión y las reconexiones en su hilo de red."""
        if not self.enabled:
            LOG.warning("MQTT deshabilitado (sin MODULINKR_MQTT_HOST): "
                        "la telemetria se queda en el buffer local")
            return

        # paho 2.x exige declarar la versión de la API de callbacks; paho 1.6
        # no la conoce. Se intenta la nueva y se cae a la vieja.
        try:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id="modulinkr-gateway", protocol=mqtt.MQTTv311)
        except (AttributeError, TypeError):
            self.client = mqtt.Client(
                client_id="modulinkr-gateway", protocol=mqtt.MQTTv311)

        if self.user:
            self.client.username_pw_set(self.user, self.password)

        # TLS: la CA (RSA) que firma el cert del broker. Sin cafile se usan
        # las CAs del sistema (broker con cert de una CA pública).
        if self.tls:
            self.client.tls_set(
                ca_certs=self.cafile or None,
                certfile=self.certfile or None,
                keyfile=self.keyfile or None,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            if self.tls_insec:
                # Solo banco: acepta el cert aunque el hostname no cuadre.
                self.client.tls_insecure_set(True)

        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
        except Exception as e:                       # noqa: BLE001
            LOG.error("no se pudo iniciar conexion MQTT a %s:%d: %s",
                      self.host, self.port, e)
        self.client.loop_start()
        LOG.info("MQTT: conectando a %s:%d (%s)", self.host, self.port,
                 "en claro" if not self.tls else
                 ("TLS insecure" if self.tls_insec else "TLS"))

    def stop(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:                        # noqa: BLE001
                pass

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self.connected = True
            LOG.info("MQTT conectado al broker cloud")
        else:
            self.connected = False
            LOG.error("MQTT rechazado por el broker (rc=%s)", rc)

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.connected = False
        if rc != 0:
            LOG.warning("MQTT desconectado (rc=%s), reintentando", rc)

    # ----- Drenado -----

    def drain(self) -> int:
        """Publica catálogos y telemetría pendientes. No bloquea si no hay
        conexión (return 0). Marca published=1 solo tras el PUBACK. Devuelve
        cuántos mensajes se confirmaron en esta vuelta. Pensado para llamarse
        periódicamente desde el bucle del servicio."""
        if not self.enabled or not self.connected:
            return 0

        confirmed = 0
        # Primero los catálogos (la leyenda antes que los datos, §10.1).
        confirmed += self._drain_catalogs()
        confirmed += self._drain_telemetry()
        return confirmed

    def _drain_catalogs(self) -> int:
        cats = self.buf.fetch_pending_catalogs()
        confirmed = 0
        for c in cats:
            origin  = c["origin_id"]
            payload = self._register_payload(origin, c["catalog"])
            topic   = REGISTER_TOPIC.format(origin=origin)
            if self._publish(topic, payload, qos=1, retain=True):
                self.buf.mark_catalog_published(origin)
                self.n_pub_cat += 1
                confirmed += 1
                LOG.info("MQTT register publicado origin=%d", origin)
            else:
                # Sin PUBACK: se queda pendiente y se reintenta la próxima
                # vuelta. No se sigue si el broker no confirma.
                break
        return confirmed

    def _drain_telemetry(self) -> int:
        rows = self.buf.fetch_pending(self.drain_max)
        confirmed_keys: list[tuple[int, int, int]] = []
        for r in rows:
            origin, ts, seq = r["origin_id"], r["ts"], r["seq"]
            payload = json.dumps({
                "schema_version": SCHEMA_VERSION,
                "origin": origin,
                "seq":    seq,
                "ts":     ts if ts else None,
                "v":      r["v"],
            }, separators=(",", ":"))
            topic = TELEMETRY_TOPIC.format(origin=origin)
            if self._publish(topic, payload, qos=1, retain=False):
                confirmed_keys.append((origin, ts, seq))
            else:
                break

        if confirmed_keys:
            self.buf.mark_published(confirmed_keys)
            self.n_pub_tel += len(confirmed_keys)
            LOG.info("MQTT telemetria publicada: %d muestra(s)",
                     len(confirmed_keys))
        return len(confirmed_keys)

    def _publish(self, topic: str, payload: str, qos: int,
                 retain: bool) -> bool:
        """Publica y espera el PUBACK hasta pub_timeout. Devuelve True si el
        broker confirmó. Ante fallo (sin conexión, timeout) devuelve False:
        el llamador deja la entrada pendiente."""
        try:
            info = self.client.publish(topic, payload, qos=qos, retain=retain)
        except Exception as e:                       # noqa: BLE001
            LOG.warning("MQTT publish fallo topic=%s: %s", topic, e)
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("MQTT publish rc=%s topic=%s", info.rc, topic)
            return False
        try:
            info.wait_for_publish(self.pub_timeout)
        except (ValueError, RuntimeError) as e:
            LOG.warning("MQTT sin PUBACK topic=%s: %s", topic, e)
            return False
        return info.is_published()

    def _register_payload(self, origin: int, catalog: dict) -> str:
        """Mensaje register retenido de batch-format.md §10.2 a partir del
        catálogo decodificado guardado en node_catalog. boot_id no viaja por
        LoRa (es identidad de sesión NB-IoT), así que va null."""
        reads = [
            {"id": rd.get("id"), "name": rd.get("name"),
             "unit": rd.get("unit") or None}
            for rd in catalog.get("reads", [])
        ]
        writes = [
            {"id": wr.get("id"), "name": wr.get("name"),
             "unit": wr.get("unit") or None}
            for wr in catalog.get("writes", [])
        ]
        return json.dumps({
            "schema_version": SCHEMA_VERSION,
            "node_id":        origin,
            "name":           catalog.get("node_name"),
            "fw_version":     catalog.get("fw_version"),
            "boot_id":        None,
            "reads":          reads,
            "writes":         writes,
        }, separators=(",", ":"))
