"""Persistencia idempotente de cambios del gateway en su despliegue."""
import math
import time
from datetime import datetime, timezone
import identity_ingest


def process(db, payload):
    rows = payload.get('gateway_metrics')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError('Lote de métricas inválido')
    values = []
    for row in rows:
        event_id, ts, duty = (row.get(k) for k in ('id','ts','duty_pct'))
        counts = [row.get(k) for k in ('online_lora','online_nbiot','online_total')]
        if (type(event_id) is not int or event_id < 1 or type(ts) not in (int,float)
                or not math.isfinite(ts) or ts < 1577836800 or ts > time.time()+300
                or type(duty) not in (int,float) or not math.isfinite(duty) or not 0<=duty<=100
                or any(type(n) is not int or not 0<=n<=254 for n in counts)
                or not max(counts[:2])<=counts[2]<=sum(counts[:2])):
            raise ValueError('Métrica de gateway inválida')
        values.append((event_id,datetime.fromtimestamp(ts,timezone.utc),duty,*counts))
    conn=identity_ingest.scoped(db,payload['deployment_id'])
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO gateway_metrics(event_id,ts,duty_pct,online_lora,online_nbiot,online_total) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',values)
    conn.commit()
