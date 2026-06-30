# ModuLinkr

Arquitectura híbrida y redundante para IoT industrial: lectura de dispositivos **Modbus RTU** por RS-485 y publicación de los datos por dos caminos inalámbricos en paralelo, **LoRa** local multi-salto y **NB-IoT** como respaldo celular.
## Estructura del monorepo

```
firmware/
├── nodo/         Firmware del nodo (Atom Lite + DTU LoRa + NB-IoT2 + Modbus)
├── supernodo/    Firmware del supernodo (Pi Zero 2W), pendiente
├── gateway/      Firmware del gateway (Pi 5), pendiente
└── shared/       Especificaciones y librerías comunes a varios roles
    └── protocol/   Formato de tramas, jerarquía MQTT, etc.
```

Cada subcarpeta tiene su propio toolchain y su propio README con instrucciones específicas.

## Estado actual

| Rol | Plataforma | Estado |
| --- | --- | --- |
| Nodo | M5Stack Atom Lite + Atom DTU LoRaWAN + NB-IoT 2 Unit + sensor XY-MD02 | En desarrollo (V1) |
| Supernodo | Raspberry Pi Zero 2W + LoRa HAT + módem celular + Modbus | Pendiente (V2) |
| Gateway | Raspberry Pi 5 + LoRa HAT | Pendiente (V2) |

