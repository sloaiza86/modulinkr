# ModuLinkr, nodo (V1)

Firmware del nodo de prueba en banco. Lee un sensor Modbus RTU y publica las medidas por dos canales redundantes: LoRa P2P (cadencia rápida, local) y NB-IoT (cadencia baja, contingencia con respaldo en la nube).

## Hardware

| Componente | Modelo | Función |
| --- | --- | --- |
| MCU | M5Stack **Atom Lite** (ESP32-PICO-D4, 520 KB SRAM, 4 MB flash) | Cerebro del nodo |
| LoRa + RS-485 | M5Stack **Atom DTU LoRaWAN US915** (STM32WLE5CC + SP3485EN) | Radio sub-GHz y bus Modbus, controlado por AT |
| Celular | M5Stack **NB-IoT 2 Unit Global** (SIM7028) | NB-IoT Cat-NB2 multi-banda, en puerto Grove |
| Sensor | **XY-MD02** | Temperatura + humedad por Modbus RTU |

> **Nota región**: el DTU disponible ahora es **US915** (el EU868 se pidió por error). El firmware se desarrolla con `REGION_US915` y se mueve a `REGION_EU868` cuando llegue el módulo correcto, cambiando un único `build_flag`.

## Esquema funcional

```
  XY-MD02
     │ (Modbus RTU, 9600 8N1, slave 1, regs 0..1)
     │
   RS-485 (A/B)
     │
  ┌──┴──────────────────┐
  │  Atom DTU LoRaWAN   │ ← STM32WLE5CC + SP3485EN
  │  - LoRa P2P         │
  │  - RS-485 bridge    │
  └──┬──────────────────┘
     │ UART (AT commands)
     │
  ┌──┴──────────────────┐         ┌──────────────────────┐
  │   Atom Lite (ESP32) │ ←Grove→ │  NB-IoT 2 Unit       │
  │                     │  UART   │  (SIM7028, Cat-NB2)  │
  │  • Tareas FreeRTOS  │  AT     └──────────────────────┘
  │  • Buffer circular  │
  │  • Consola USB      │ → CP2104 → /dev/cu.usbserial-*
  └─────────────────────┘
```

## Plan de hitos

| Hito | Descripción | Estado |
| --- | --- | --- |
| H0 | Estructura inicial del repo, stub compilable | Completado (tag `v0.0.1-h0`) |
| H1 | Modbus → consola: lectura XY-MD02 cada 1 s, volcado por `Serial` | Completado (tag `v0.0.2-h1`) |
| H2 emisor | LoRa P2P, solo TX: trama TELEMETRY según `frame-format.md` cada 1 s tras lectura Modbus OK | Completado (tag `v0.0.4-h2-tx`) |
| H2 receptor | LoRa P2P, RX con segundo DTU o SDR para validar payload extremo a extremo y emitir ACKs | Pendiente |
| H3 | NB-IoT en aislamiento: attach + 1 publish MQTT manual | Pendiente |
| H4 | H1 + H2 integrados (cola de ACKs, gestión de timeouts) | Pendiente |
| H5 | Añadir tarea NB-IoT con cola FreeRTOS compartida y batch JSON periódico (modo prueba de concepto cada 5 min) | Pendiente |
| H6 | Consola estructurada con timestamps, LED de estado, manejo de errores | Pendiente |

## Cadencia de envío y duty cycle

| Canal | Cadencia objetivo | Notas |
| --- | --- | --- |
| LoRa | cada 1 s, US915 en banco; cada 1 s en EU868 g3 (869.525 MHz) al portar | SF7 BW125. Trama TELEMETRY con 2 reads = 16 bytes (ver `shared/protocol/frame-format.md` §6), ToA ≈ 57 ms |
| NB-IoT, fase prueba de concepto (actual) | cada 5 min | Publica un batch periódico para validar que LoRa y NB-IoT conviven sin interferirse |
| NB-IoT, fase operacional (post-validación) | respaldo selectivo | Se activa solo cuando una racha de ACKs LoRa falla. Mecanismo en `shared/protocol/node-config.md` §4.3 |
| Consola | continua, formato append con timestamp | Sin cadencia fija. Cada evento imprime una línea |

## Compilar y flashear

Pre-requisitos: PlatformIO instalado en VS Code (extension `PlatformIO IDE`).

```bash
# Desde la raíz del nodo
pio run                            # compilar
pio run --target upload            # flashear
pio device monitor --filter time   # monitor con timestamps
```

Atajos en VS Code: `PlatformIO: Build`, `PlatformIO: Upload`, `PlatformIO: Monitor`.

## Estructura

```
nodo/
├── platformio.ini          Configuración del proyecto (incluye REGION_*, MODEM_*, LORA_TX_DBM, NODE_ID)
├── src/
│   ├── main.cpp            Punto de entrada, ciclo Modbus + LoRa (existe)
│   ├── modbus.{cpp,h}      Driver Modbus RTU sobre RS-485 (existe)
│   ├── lora.{cpp,h}        Driver LoRa P2P sobre la librería M5-LoRaWAN-RAK (existe, modo TX)
│   ├── nbiot.{cpp,h}       Driver NB-IoT sobre AT del SIM7028 (pendiente, H3)
│   ├── buffer.{cpp,h}      Cola circular FreeRTOS para tramas con ACK pendiente (pendiente, H4)
│   └── console.{cpp,h}     Logging estructurado por Serial (pendiente, H6)
└── lib/                    Librerías propias específicas del nodo
```

Las constantes regionales (`REGION_US915` / `REGION_EU868`) y los parámetros del nodo (`NODE_ID`, `LORA_TX_DBM`) viven en `platformio.ini` como `build_flags`, no en un header dedicado.
