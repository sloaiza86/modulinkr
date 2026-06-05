# ModuLinkr — Nodo (V1)

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
| H0 | Estructura inicial del repo, stub compilable | En curso |
| H1 | Modbus → consola: lectura XY-MD02 cada 1 s, volcado por `Serial` | Pendiente |
| H2 | LoRa P2P en aislamiento: envío de payload mínimo cada N s al canal de prueba | Pendiente |
| H3 | NB-IoT en aislamiento: attach + 1 publish MQTT manual | Pendiente |
| H4 | H1 + H2 integrados en una sola tarea | Pendiente |
| H5 | Añadir `taskNbiot` con cola FreeRTOS compartida y batch JSON cada 5 min | Pendiente |
| H6 | Consola estructurada con timestamps, LED de estado, manejo de errores | Pendiente |

## Cadencia de envío y duty cycle

| Canal | Cadencia objetivo | Notas |
| --- | --- | --- |
| LoRa | cada 1 s (US915 en banco), cada 1 s (EU868 g3 869.525 MHz al portar) | SF7 BW125, payload mínimo 4 B → ToA ≈ 41 ms |
| NB-IoT | cada 5 min | Batch con las ~300 muestras anteriores serializadas como JSON |
| Consola | continua, formato append con timestamp | Sin cadencia fija — cada evento imprime una línea |

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
├── platformio.ini          Configuración del proyecto
├── src/
│   ├── main.cpp            Punto de entrada, setup + tareas
│   ├── region.h            Constantes regionales (LoRa freq, potencia, etc.)
│   ├── modbus.{cpp,h}      Driver Modbus RTU sobre AT del DTU
│   ├── lora.{cpp,h}        Driver LoRa P2P sobre AT del DTU
│   ├── nbiot.{cpp,h}       Driver NB-IoT sobre AT del SIM7028
│   ├── buffer.{cpp,h}      Cola circular FreeRTOS para muestras
│   └── console.{cpp,h}     Logging estructurado por Serial
└── lib/                    Librerías propias específicas del nodo
```

Los archivos `.cpp/.h` se irán creando conforme avancemos por los hitos. Para H0 solo existe `main.cpp` como stub.
