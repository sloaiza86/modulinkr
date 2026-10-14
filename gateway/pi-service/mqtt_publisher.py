#!/usr/bin/env python3
"""Publica catálogos, muestras y salud con identidad física del despliegue.

Los topics utilizan modulinkr/v2/<deployment_id>/<publicador>. Se confirma
el buffer solo después del PUBACK y se observa la entrega celular sin
republicar las muestras recibidas del broker.
"""

from __future__ import annotations

import collections
import identity
import json
import logging
import os
import ssl
import time

import paho.mqtt.client as mqtt

LOG = logging.getLogger("modulinkr.mqtt")

SCHEMA_VERSION   = "4.0"
SERVICE_VERSION  = "0.1.0"     # versión del servicio del Pi (debug.fw_version)
GATEWAY_ID       = 255         # publisher del gateway (0xFF, frame-format §1.5)
# El gateway se suscribe a la telemetría de TODOS los publishers para ver el
# camino NB-IoT (failover): un publisher distinto de 255 es un supernodo
# publicando por celular (db-schema §2, source por el publisher del topic).


class MqttPublisher:
    def __init__(self, buf):
        self._catalog_q=collections.deque()
        self.buf = buf
        row=buf.conn.execute('SELECT uid FROM identity_bindings WHERE origin=255').fetchone()
        self.deployment=row[0] if row else identity.host_uid(buf.conn)
        self.topic_prefix=f'modulinkr/v2/{self.deployment}'
        self.client_id=f'ml-gateway-{self.deployment}' 

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
        self.debug_env  = os.environ.get("MODULINKR_MQTT_DEBUG", "1") == "1"

        # Sin host no hay cloud: el servicio sigue operando (recibe LoRa,
        # ACK, beacon) y las muestras se acumulan en el buffer local.
        self.enabled   = bool(self.host)
        self.client: mqtt.Client | None = None
        self.connected = False

        # Contadores de diagnóstico (se vuelcan en el STATS del servicio).
        self.n_pub_tel = 0   # muestras de telemetría publicadas (con PUBACK)
        self.n_pub_cat = 0   # catálogos republicados (con PUBACK)
        self.n_pub_hlt = 0   # tramas de salud publicadas (con PUBACK)
        self.batch_id  = 0   # mensajes de telemetría enviados en esta sesión
        self._publishing = {}
        self._health_pending = {}

        # Cola del camino NB-IoT: on_message corre en el hilo de paho y no
        # puede tocar el SQLite del buffer (un solo hilo). Encola y el bucle
        # principal escribe con drain_nbiot. Acotada para no crecer sin fin.
        self._nbiot_q: collections.deque = collections.deque(maxlen=2000)
        self._command_q = collections.deque(maxlen=128)

    # ----- Conexión -----

    def start(self) -> None:
        """Abre la conexión al broker en segundo plano. No bloquea: paho
        gestiona la conexión y las reconexiones en su hilo de red."""
        if not self.enabled:
            LOG.warning("event=mqtt.disabled reason=missing_host fallback=local_buffer")
            return

        # paho 2.x exige declarar la versión de la API de callbacks; paho 1.6
        # no la conoce. Se intenta la nueva y se cae a la vieja.
        try:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.client_id, protocol=mqtt.MQTTv311)
        except (AttributeError, TypeError):
            self.client = mqtt.Client(
                client_id=self.client_id, protocol=mqtt.MQTTv311)

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
        self.client.on_message    = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
        except Exception as e:                       # noqa: BLE001
            LOG.error("event=mqtt.connection_start_failed host=%s port=%d error=%s",
                      self.host, self.port, e)
        self.client.loop_start()
        LOG.info("event=mqtt.connecting host=%s port=%d transport=%s", self.host, self.port,
                 "tcp" if not self.tls else
                 ("tls-insecure" if self.tls_insec else "tls"))

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
            LOG.info("event=mqtt.connected")
            # Suscripción a la telemetría de todos los publishers (camino
            # NB-IoT/failover). Se resuscribe en cada reconexión.
            try:
                client.subscribe(f"{self.topic_prefix}/+/telemetry", qos=1)
                client.subscribe(f"{self.topic_prefix}/+/cmd/response", qos=1)
                client.subscribe(f"{self.topic_prefix}/+/register/#",qos=1)
            except Exception as e:                    # noqa: BLE001
                LOG.warning("event=mqtt.subscribe_failed error=%s", e)
        else:
            self.connected = False
            LOG.error("event=mqtt.connection_rejected rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc) -> None:
        self.connected = False
        if rc != 0:
            LOG.warning("event=mqtt.disconnected rc=%s retry=true", rc)

    def _on_message(self, client, userdata, msg) -> None:
        """Telemetría oída del broker. Un publisher distinto del gateway es un
        supernodo publicando por NB-IoT (failover): se encola su actividad y
        el último dato de cada origin, para que el bucle principal los guarde
        (drain_nbiot). Corre en el hilo de paho: solo encola, no toca SQLite,
        y nunca propaga (un fallo aquí no debe tumbar el bucle de red)."""
        try:
            parts=msg.topic.split('/')
            if len(parts)<5 or '/'.join(parts[:3])!=self.topic_prefix: return
            publisher = int(parts[3])
            if not 1 <= publisher <= 254 or (msg.retain and parts[4]!='register'):
                return  # Una publicación retenida no confirma actividad actual.
            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
            if payload.get("deployment_id")!=self.deployment or payload.get("schema_version")!="4.0": return
            if msg.topic == f"{self.topic_prefix}/{publisher}/cmd/response":
                if payload.get('schema_version')=='4.0' and type(payload.get('origin')) is int:
                    self._command_q.append((publisher,payload))
                return
            if parts[4]=='register':
                if payload.get('node_id')==publisher and 'catalog_fragment' in payload: self._catalog_q.append(payload)
                return
            samples = []
            for s in payload.get("samples") or []:
                origin, ts, v = s.get("origin"), s.get("ts"), s.get("v")
                if not isinstance(origin, int) or not isinstance(ts, int) or v is None:
                    continue
                st = s.get("st")
                samples.append(
                    (origin, ts, json.dumps(dict(v=v,st=st,device_uid=s.get("device_uid"),boot_id=s.get("boot_id"),catalog_tag=s.get("catalog_tag"))), s.get("seq",0), s.get("path"), s.get("path_at"),s,payload.get("publisher_uid"),payload.get("publisher_boot")))
            if samples:
                self._nbiot_q.append((publisher, samples, time.time()))
        except Exception as e:                        # noqa: BLE001
            LOG.warning("event=mqtt.message_rejected error=%s", e)

    def drain_nbiot(self) -> None:
        """Vuelca la cola del camino NB-IoT al buffer (hilo principal). Marca
        la actividad del supernodo (mqtt_seen) y el último dato de cada origin
        (nbiot_last). Barato: se llama cada vuelta del bucle del servicio."""
        self.poll_publications()
        while self._catalog_q:
            catalog_payload=self._catalog_q.popleft()
            try:
                origin=catalog_payload['node_id']
                if not identity.observe(self.buf.conn,origin,catalog_payload.get('device_uid'),catalog_payload.get('boot_id',1)):
                    self.buf.conn.commit(); continue
                self.buf.conn.commit()
                blob=identity.assemble_catalog(self.buf.conn,catalog_payload)
                if blob is None: continue
                import protocol
                cat=protocol.parse_catalog(blob)
                if 'error' in cat: raise ValueError(cat['error'])
                cat.update(device_uid=catalog_payload['device_uid'],catalog_tag=catalog_payload['catalog_tag'])
                self.buf.catalog_upsert(origin,cat)
            except (KeyError,ValueError) as exc:
                identity.incident(self.buf.conn,'catalog_conflict',catalog_payload.get('node_id',0),str(exc));self.buf.conn.commit()
        while True:
            try:
                publisher, samples, received_at = self._nbiot_q.popleft()
            except IndexError:
                break
            for values in samples:
                origin,ts,reads_json,seq,path,path_at=values[:6]
                if len(values)>6:
                    sample,publisher_uid,publisher_boot=values[6:]
                    self.buf.conn.execute('BEGIN IMMEDIATE')
                    sender_ok=identity.observe(self.buf.conn,publisher,publisher_uid,publisher_boot)
                    origin_ok=identity.observe(self.buf.conn,origin,sample.get('device_uid'),sample.get('boot_id'))
                    self.buf.conn.commit()
                    if not sender_ok or not origin_ok or identity.blocked(self.buf.conn,origin):
                        identity.quarantine(self.buf.conn,sample,'identity_conflict'); continue
                if len(values)>6 and not self.buf.conn.execute('SELECT 1 FROM identity_catalogs WHERE uid=? AND tag=?',(sample.get('device_uid'),sample.get('catalog_tag'))).fetchone():
                    identity.incident(self.buf.conn,'catalog_missing',origin,'El dato celular anuncia un catálogo que todavía no se ha recibido.')
                    identity.quarantine(self.buf.conn,sample,'catalog_missing'); continue
                self.buf.nbiot_last_update(origin, ts, reads_json, publisher, received_at)
                self.buf.observe_route(origin,ts,seq,"nbiot",publisher,path,path_at,received_at)
            self.buf.mqtt_seen(publisher, received_at)

    # ----- Drenado -----

    def drain(self) -> int:
        """Encola catálogos y telemetría. La confirmación se recoge sin bloquear."""
        if not self.enabled or not self.connected:
            return 0

        self.poll_publications()
        self._drain_catalogs()
        self._drain_health()
        return self._drain_telemetry()

    def _drain_catalogs(self) -> int:
        for uid,tag,origin,raw in self.buf.conn.execute('SELECT uid,tag,origin,catalog_json FROM identity_catalogs WHERE published=0').fetchall():
            if identity.blocked(self.buf.conn,origin): continue
            payload=self._register_payload(origin,json.loads(raw))
            topic=f'{self.topic_prefix}/{origin}/register/{tag}'
            def saved(uid=uid,tag=tag):
                self.buf.conn.execute('UPDATE identity_catalogs SET published=1 WHERE uid=? AND tag=?',(uid,tag))
                self.buf.conn.commit()
            self._publish(topic,payload,qos=1,retain=True,confirmed=saved)
        for c in self.buf.fetch_pending_catalogs():
            origin = c["origin_id"]
            if identity.blocked(self.buf.conn,origin): continue
            payload = self._register_payload(origin, c["catalog"])
            topic = f"{self.topic_prefix}/{origin}/register"
            def confirmed(origin=origin, payload=payload):
                # Un registro nuevo durante el envío necesita su propio PUBACK.
                current = next((c for c in self.buf.fetch_pending_catalogs()
                                if c["origin_id"] == origin), None)
                if current and self._register_payload(origin, current["catalog"]) == payload:
                    self.buf.mark_catalog_published(origin)
                self.n_pub_cat += 1
                LOG.info("event=mqtt.register_published origin=%d", origin)
            self._publish(topic, payload, qos=1, retain=True, confirmed=confirmed)
        return 0

    def _drain_telemetry(self) -> int:
        """Publica las muestras pendientes de la vuelta como UN mensaje
        unificado (batch-format.md §3): un solo publish y un solo PUBACK
        para hasta drain_max muestras. Solo tras el PUBACK se marcan
        published; sin PUBACK, el mismo lote permanece en vuelo y las
        muestras nuevas esperan al siguiente lote."""
        if f"{self.topic_prefix}/255/telemetry" in self._publishing:
            return 0
        rows = self.buf.fetch_pending(self.drain_max,verified_only=True)
        if not rows:
            return 0

        verified=[r for r in rows if identity.valid_uid(r.get('device_uid')) and r.get('boot_id') and r.get('catalog_tag')]
        for row in rows:
            if row not in verified:
                identity.quarantine(self.buf.conn,row,'invalid_sample_identity')
        rows=verified
        if not rows: return 0
        # Orden de batch-format.md §4.1: por origin y seq ascendente
        # (fetch_pending viene por t_recv; con un solo origen coincide).
        rows.sort(key=lambda r: (r["origin_id"], r["seq"]))

        # v puede llevar null (lectura fallida) y st acompaña cuando
        # hay estados distintos de ok (batch-format.md §4).
        samples = []
        for r in rows:
            s = {"origin": r["origin_id"], "seq": r["seq"],
                 "ts": r["ts"], "v": r["v"]}
            if r.get("st"):
                s["st"] = r["st"]
            if r.get("path"):
                s["path"] = r["path"]
                s["path_at"] = r["path_at"]
            s.update(device_uid=r.get("device_uid"),boot_id=r.get("boot_id"),catalog_tag=r.get("catalog_tag"))
            samples.append(s)
        msg = {
            "schema_version": "4.0",
            "deployment_id":self.deployment,"publisher_uid":self.deployment,
            "samples":        samples,
        }
        if self.debug_env:
            msg["debug"] = {
                "publisher":  GATEWAY_ID,
                "batch_id":   self.batch_id + 1,
                "trigger":    "gateway",
                "fw_version": SERVICE_VERSION,
            }

        payload = json.dumps(msg, separators=(",", ":"))
        keys = [(r["origin_id"], r["ts"], r["seq"],r["device_uid"],r["boot_id"]) for r in rows]
        batch_id = self.batch_id + 1
        def confirmed():
            self.buf.mark_published(keys)
            self.n_pub_tel += len(keys)
            LOG.info("event=mqtt.telemetry_published samples=%d messages=1 batch_id=%d",
                     len(keys), batch_id)
        if self._publish(f"{self.topic_prefix}/255/telemetry", payload, qos=1, retain=False, confirmed=confirmed):
            self.batch_id = batch_id
        return 0

    def _publish(self, topic: str, payload: str, qos: int,
                 retain: bool, confirmed=None) -> bool:
        """Encola sin esperar al broker. SQLite se confirma desde poll_publications."""
        if not self.enabled or not self.connected or topic in self._publishing:
            return False
        try:
            info = self.client.publish(topic, payload, qos=qos, retain=retain)
        except Exception as e:
            LOG.warning("event=mqtt.publish_failed topic=%s error=%s", topic, e)
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("event=mqtt.publish_rejected rc=%s topic=%s", info.rc, topic)
            return False
        self._publishing[topic] = {"info": info, "confirmed": confirmed,
                                   "started": time.monotonic(), "warned": False}
        return True

    def poll_publications(self) -> None:
        # Paho recibe los PUBACK en su hilo; este hilo conserva todas las
        # escrituras SQLite. El lote en vuelo no incorpora medidas posteriores.
        for topic, flight in list(self._publishing.items()):
            try:
                done = flight["info"].is_published()
            except (ValueError, RuntimeError):
                del self._publishing[topic]
                continue
            if done:
                if flight["confirmed"]:
                    flight["confirmed"]()
                del self._publishing[topic]
            elif not flight["warned"] and time.monotonic() - flight["started"] >= self.pub_timeout:
                flight["warned"] = True
                # Se conserva la custodia y el mensaje QoS 1 de Paho, que se
                # reenvía al reconectar. No se acumulan copias por cada vuelta.
                LOG.warning("event=mqtt.puback_timeout topic=%s pending=true", topic)

    def _drain_health(self) -> None:
        for origin, payload in list(self._health_pending.items()):
            def confirmed(origin=origin, payload=payload):
                if self._health_pending.get(origin) == payload:
                    del self._health_pending[origin]
                self.n_pub_hlt += 1
            self._publish(f"{self.topic_prefix}/{origin}/health", payload, qos=1,
                          retain=False, confirmed=confirmed)

    def publish_health(self, origin: int, health: dict) -> bool:
        """Publica una trama NODE_HEALTH (frame-format.md §16) al topic de
        salud del nodo. Sin retain: es un evento con su instante, no un
        estado actual, y el histórico lo guarda el consumer.

        Se llama desde el bucle principal, el mismo hilo que drain(), así que
        no hay concurrencia sobre el cliente.
        """
        payload = json.dumps({
            "schema_version": "4.0",
            "deployment_id":self.deployment,
            "node_id":        origin,
            "fault":          health.get("hl_fault_name"),
            "reset_reason":   health.get("hl_reset_reason"),
            "boots":          health.get("hl_boots"),
            "recoveries": {
                "probe":  health.get("hl_probes"),
                "reinit": health.get("hl_reinits"),
                "reset":  health.get("hl_resets"),
                "reboot": health.get("hl_reboots"),
            },
            "radio": {
                "tx_psend": health.get("hl_tx_psend"),
                "tx_done":  health.get("hl_tx_done"),
                "rx_valid": health.get("hl_rx_valid"),
            },
        }, separators=(",", ":"))

        self._health_pending[origin] = payload
        self._drain_health()
        return True

    def _register_payload(self, origin: int, catalog: dict) -> str:
        """Mensaje register retenido de batch-format.md §10.2 a partir del
        catálogo decodificado guardado en node_catalog."""
        payload=dict(schema_version='4.0',deployment_id=self.deployment,node_id=origin,
                     device_uid=catalog.get('device_uid'),catalog_tag=catalog.get('catalog_tag'),
                     name=catalog.get('node_name'),fw_version=catalog.get('fw_version'),
                     reads=catalog.get('reads',[]),writes=catalog.get('writes',[]))
        replacement=self.buf.conn.execute('SELECT uid,reason FROM identity_resolutions WHERE origin=? ORDER BY id DESC LIMIT 1',(origin,)).fetchone()
        if replacement and replacement[0]==catalog.get('device_uid'):
            payload['identity_resolution']=replacement[1]
            previous=self.buf.conn.execute('SELECT uid FROM identity_sightings WHERE origin=? AND uid<>? ORDER BY last_seen DESC LIMIT 1',(origin,replacement[0])).fetchone()
            if previous: payload['replaces_uid']=previous[0]
        return json.dumps(payload,separators=(',',':'))

    def publish_command(self, publisher, payload):
        reason=identity.blocked(self.buf.conn,publisher)
        if reason: return False
        return self._publish(f"{self.topic_prefix}/{publisher}/cmd",payload,qos=1,retain=False)

    def drain_commands(self, service):
        import node_commands
        while self._command_q:
            publisher,result=self._command_q.popleft()
            node_commands.receive(service,result['origin'],result,'nbiot',publisher)
