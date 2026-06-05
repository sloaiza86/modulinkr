# Formato de tramas LoRa — ModuLinkr

> **Estado**: placeholder. La especificación se cierra en el hito H2 del nodo, cuando hagamos las primeras transmisiones LoRa reales y validemos el formato contra un receptor.

## Decisiones pendientes para H2

- **Identificador del nodo**: ¿uint8 (256 nodos) o uint16 (65k nodos)? Para V1 con un único nodo de prueba, uint8 sobra.
- **Timestamp**: ¿absoluto (epoch uint32) o delta desde último envío (uint16 segundos)? Absoluto exige sincronización; delta no, pero requiere ack del receptor.
- **Codificación de medidas**: int16 con escala ×10 (resolución 0.1 ºC / 0.1 %HR) — igual que el XY-MD02 entrega por Modbus, sin re-escala.
- **CRC**: ¿confiamos en el CRC del PHY de LoRa, o añadimos CRC16 de aplicación para detectar corrupción dentro del payload? Recomendado añadir CRC16 propio.

## Borrador inicial (a confirmar en H2)

```
byte:   0     1     2     3     4     5     6     7     8     9    10    11
        ┌─────┬───────────┬───────────┬───────────────────┬─────────┐
        │ ver │ node_id   │ t_x10     │ h_x10             │ crc16   │
        │ u8  │ u8        │ i16 LE    │ u16 LE            │ u16 LE  │
        └─────┴───────────┴───────────┴───────────────────┴─────────┘
                                                  total: 8 bytes
```

- `ver`: versión del formato (0x01 inicial). Permite evolución futura sin romper compatibilidad.
- `node_id`: identificador único del nodo emisor (provisional 0x01 para el banco).
- `t_x10`: temperatura en décimas de grado Celsius, complemento a 2 (rango −3276,8 a +3276,7 ºC).
- `h_x10`: humedad relativa en décimas de porcentaje (rango 0 a 6553,5 %).
- `crc16`: CRC-16/CCITT-FALSE sobre los bytes 0..7.

## Topic MQTT (NB-IoT)

> **Estado**: placeholder, se cierra en H3.

Borrador inicial:

```
modulinkr/v1/nodo/<node_id>/batch    (cada 5 minutos)
modulinkr/v1/nodo/<node_id>/status   (eventos de estado)
```

Payload `/batch` como JSON con array de muestras y metadatos del nodo (RSSI, contador de envíos LoRa, contador de fallos Modbus).
