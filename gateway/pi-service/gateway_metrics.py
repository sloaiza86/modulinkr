"""Histórico de ocupación del transmisor y conectividad de Red Mesh."""
import json
import time
import node_commands


def initialize(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS gateway_airtime(ts REAL NOT NULL, toa_ms REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS gateway_airtime_ts ON gateway_airtime(ts);
        CREATE TABLE IF NOT EXISTS gateway_metrics(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            duty_pct REAL NOT NULL, online_lora INTEGER NOT NULL,
            online_nbiot INTEGER NOT NULL, online_total INTEGER NOT NULL,
            connections TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
    ''')


def record(service, toa_ms=None):
    c = service.buf.conn
    now = time.time()
    if toa_ms is not None:
        c.execute('INSERT INTO gateway_airtime VALUES(?,?)', (now, toa_ms))
    c.execute('DELETE FROM gateway_airtime WHERE ts<=?', (now-3600,))
    duty = c.execute('SELECT COALESCE(SUM(toa_ms),0)/36000.0 FROM gateway_airtime').fetchone()[0]
    nodes = c.execute('SELECT origin FROM identity_bindings WHERE origin BETWEEN 1 AND 254').fetchall()
    connections = []
    for (origin,) in nodes:
        lora = bool(service.ser is not None and node_commands.route(c, origin, 'lora', now))
        cellular = bool(service.mqtt and service.mqtt.connected and node_commands.route(c, origin, 'nbiot', now))
        connections.append((origin, lora, cellular))
    # Las vías se cuentan por separado; un nodo con dos rutas cuenta una vez en el total.
    signature = json.dumps(sorted(connections), separators=(',', ':'))
    counts = (sum(n[1] for n in connections), sum(n[2] for n in connections), sum(n[1] or n[2] for n in connections))
    previous = c.execute('SELECT duty_pct,connections FROM gateway_metrics ORDER BY id DESC LIMIT 1').fetchone()
    if previous is None or (duty, signature) != previous:
        c.execute('INSERT INTO gateway_metrics(ts,duty_pct,online_lora,online_nbiot,online_total,connections) VALUES(?,?,?,?,?,?)', (now, duty, *counts, signature))
    c.commit()


def publish(publisher):
    c = publisher.buf.conn
    rows = c.execute('SELECT id,ts,duty_pct,online_lora,online_nbiot,online_total FROM gateway_metrics WHERE published=0 ORDER BY id LIMIT 100').fetchall()
    if not rows:
        return
    payload = json.dumps(dict(schema_version='4.0', deployment_id=publisher.deployment,
        gateway_metrics=[dict(zip(('id','ts','duty_pct','online_lora','online_nbiot','online_total'), row)) for row in rows]), separators=(',', ':'))
    def confirmed():
        c.executemany('UPDATE gateway_metrics SET published=1 WHERE id=?', [(r[0],) for r in rows])
        c.commit()
    publisher._publish(f'{publisher.topic_prefix}/255/metrics', payload, qos=1, retain=False, confirmed=confirmed)
