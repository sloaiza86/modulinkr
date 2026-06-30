# ModuLinkr, nodo (V1)

Firmware del nodo de prueba en banco. Lee un sensor Modbus RTU y publica las medidas por dos canales redundantes: LoRa P2P y NB-IoT.

## Hardware

| Componente | Modelo | Función |
| --- | --- | --- |
| MCU | M5Stack **Atom Lite** (ESP32-PICO-D4, 520 KB SRAM, 4 MB flash) | Cerebro del nodo |
| LoRa + RS-485 | M5Stack **Atom DTU LoRaWAN EU868** (STM32WLE5CC + SP3485EN) | Radio sub-GHz y bus Modbus, controlado por AT |
| Celular | M5Stack **NB-IoT 2 Unit Global** (SIM7028) | NB-IoT Cat-NB2 multi-banda, en puerto Grove |
| Sensor | **XY-MD02** | Temperatura + humedad por Modbus RTU |

> **Nota región**: el firmware se compila con `REGION_EU868` desde la portación del 30 de junio de 2026. La región US915 sigue soportada como `build_flag` alternativo en `platformio.ini` por si se necesitara recompilar para hardware de la primera tanda.

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
| H2 receptor | LoRa P2P, RX con segundo DTU o SDR para validar payload extremo a extremo y emitir ACKs | Pendiente (a la espera de segundo DTU o RTL-SDR) |
| H3 fase 2a | NB-IoT en aislamiento: attach + MQTT publish periódico al broker público de HiveMQ | Completado en la sesión del 16 de junio de 2026 (sin tag, fase intermedia) |
| H3 fase 2b | Ciclo dual LoRa + NB-IoT alternando cada 2,5 s con medición fresca por canal | Completado (tag `v0.0.5-h3-dual`). Conflicto de UART resuelto por Opción B (ver §"Resolución del conflicto de UART") |
| H4 | Integrar Modbus + LoRa + NB-IoT en una sola tarea FreeRTOS con cola de ACKs | Pendiente |
| H5 | Tarea NB-IoT con cola FreeRTOS compartida y batch JSON periódico (modo prueba de concepto cada 5 min) | Pendiente |
| H6 | Consola estructurada con timestamps, LED de estado, manejo de errores | Pendiente |

## Resolución del conflicto de UART

El microcontrolador ESP32-PICO-D4 del Atom Lite expone **tres UART hardware**: UART0, UART1 y UART2. UART0 lo usa el puente CP2104 para la consola USB y queda fuera de discusión. Quedan UART1 y UART2 libres para periféricos. El nodo final del proyecto requiere comunicarse con **tres subsistemas serie**:

| Subsistema | Velocidad | Pines actuales | UART asignado en cada hito |
| --- | --- | --- | --- |
| Modbus RTU (SP3485EN del DTU LoRa) | 9600 8N1 | GPIO 33 RX / GPIO 23 TX | `Serial1` desde H1 |
| LoRa P2P (STM32WLE5 del DTU LoRa) | 115200 8N1 | GPIO 19 RX / GPIO 22 TX | `Serial2` desde H2 |
| NB-IoT (SIM7028 del Unit Grove) | 115200 8N1 | GPIO 32 RX / GPIO 26 TX | `Serial2` durante H3 fase 2a |

Tres subsistemas, dos UART hardware. **El conflicto es estructural**: no caben todos a la vez.

### Estado actual

H3 fase 2b validado con **Opción B**: Modbus migrado a SoftwareSerial a 9600 baud, los dos UART hardware quedan para LoRa (`Serial1`) y NB-IoT (`Serial2`) a 115200. La pasada de validación en banco mostró cero errores Modbus en ciclos consecutivos tras un warmup inicial de tres lecturas descartadas.

### Opciones evaluadas (Opción B elegida)

**Opción A**: bajar el baud del SIM7028 a 9600 y migrar NB-IoT a `SoftwareSerial`.

Se ejecuta `AT+IPR=9600;&W` una vez para que el SIM7028 guarde 9600 baud en memoria no volátil. A partir de ahí el firmware abre el SIM7028 desde la librería `EspSoftwareSerial` (o equivalente) por GPIO 32/26, y `Serial2` queda disponible para LoRa.

Pro: cambio puramente software, hardware intacto.
Contra: `SoftwareSerial` en ESP32 pierde bytes esporádicamente incluso a 9600 baud; los AT del SIM7028 con URC asíncronas (`+CEREG`, `+CMQTT...`) podrían quedar truncados.

**Opción B**: mover el driver Modbus a `SoftwareSerial`.

`Serial1` se libera para NB-IoT a 115200 (UART hardware fiable). Modbus a 9600 sobre `SoftwareSerial` es la combinación más tolerante porque Modbus tiene timing relajado y los frames son cortos.

Pro: NB-IoT y LoRa quedan en UART hardware, donde más importa la fiabilidad.
Contra: requiere repinear el RS-485 del DTU si el firmware del STM32WLE5 expone los pines de RS-485 al header. Hay que verificar si el `Atom DTU LoRaWAN US915` permite reasignar `RS485_RX` y `RS485_TX` a otros GPIO del Atom.

**Opción C**: multiplexar `Serial2` en software entre LoRa y NB-IoT entre ciclos.

`Serial2` cambia de pines y baud según toque mandar trama LoRa o publicar MQTT. Frágil por las latencias de re-arranque del UART (varias decenas de ms) y por la cola de bytes pendientes que se pierde al hacer el switch. No recomendado.

**Opción D**: cambiar a un MCU con más UART hardware.

El M5Stack **AtomS3 Lite** lleva ESP32-S3 con dos UART hardware adicionales y USB-OTG nativo (sin necesidad de puente). Encaja todo sin conflictos.

Pro: solución limpia desde el día 1.
Contra: cambio de hardware en mitad del proyecto, implica revisar el firmware y rehacer las pruebas de H0 a H3 en el nuevo MCU.

### Decisión aplicada

**Opción B** quedó adoptada en `v0.0.5-h3-dual`. Las razones técnicas que la favorecieron sobre A:

- Modbus tolera mejor el timing impreciso de SoftwareSerial: las tramas son cortas, el CRC16 cierra la integridad, los reintentos por timeout son baratos.
- NB-IoT y LoRa quedan en UART hardware donde URC asíncronas y AT cargados aprovechan la velocidad de 115200 sin riesgo de pérdida de bytes.
- No requiere modificar el baud del SIM7028 en memoria no volátil (Opción A obligaba a `AT+IPR=9600;&W`).

El único cuidado añadido es un warmup de tres lecturas descartadas en setup tras `modbus.begin()`, para que el ISR de SoftwareSerial se estabilice antes de que el scheduler dual dispare la primera trama LoRa.

Opciones C y D quedan reservadas como camino futuro si el escalado del proyecto lo justifica (multi-sensor, multi-canal, MCU con más UART hardware).

## Cadencia de envío y duty cycle

| Canal | Cadencia objetivo | Notas |
| --- | --- | --- |
| LoRa | cada 1 s en EU868 g3 (869.525 MHz) | SF7 BW125. Trama TELEMETRY con 2 reads = 16 bytes (ver `shared/protocol/frame-format.md` §6), ToA ≈ 57 ms |
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
├── platformio.ini          Configuración del proyecto (incluye REGION_*, MODEM_*, LORA_TX_DBM, NODE_ID, NBIOT_APN, MQTT_*)
├── src/
│   ├── main.cpp            Punto de entrada (varía según el hito en curso)
│   ├── modbus.{cpp,h}      Driver Modbus RTU sobre RS-485 (existe)
│   ├── lora.{cpp,h}        Driver LoRa P2P sobre la librería M5-LoRaWAN-RAK (existe, modo TX)
│   ├── nbiot.{cpp,h}       Driver NB-IoT sobre AT del SIM7028 con MQTT nativo (existe, modo single-channel)
│   ├── buffer.{cpp,h}      Cola circular FreeRTOS para tramas con ACK pendiente (pendiente, H4)
│   └── console.{cpp,h}     Logging estructurado por Serial (pendiente, H6)
└── lib/                    Librerías propias específicas del nodo
```

Las constantes regionales (`REGION_US915` / `REGION_EU868`) y los parámetros del nodo (`NODE_ID`, `LORA_TX_DBM`) viven en `platformio.ini` como `build_flags`, no en un header dedicado.
