# ModuLinkr, firmware de nodo y supernodo

El directorio contiene el firmware común de los dispositivos de campo ModuLinkr.

## Estado actual

La versión declarada en `src/main.cpp` es `0.0.58-difusion-red`. El firmware usa la trama LoRa `0x39` y acepta configuraciones con `schema_version` de `3.0` a `3.3`. Las configuraciones de banco `configs/nodo1.json` y `configs/nodo2.json` usan `3.3`.

El rol no se decide al compilar. `node.type` y los demás campos de `/config.json` determinan si el equipo opera como nodo o supernodo, qué dispositivo Modbus lee y qué parámetros de red utiliza.

## Hardware del banco

| Rol | Componentes |
| --- | --- |
| Nodo | M5Stack Atom Lite, Atom DTU LoRaWAN EU868 y XY-MD02 |
| Supernodo | M5Stack Atom Lite, Atom DTU LoRaWAN EU868, NB-IoT 2 Unit SIM7028 y WT901C485 |

El Atom Lite usa SoftwareSerial para Modbus y reserva las UART hardware para LoRa y NB-IoT. El bloque NB-IoT solo se inicia cuando la configuración declara el rol de supernodo.

## Comportamiento

El dispositivo obtiene hora antes de muestrear, se registra en la red y anuncia su catálogo. La telemetría incluye timestamp, valores y estado de cada transacción Modbus. Las muestras no confirmadas permanecen en la outbox y pueden entregarse mediante custodia NB-IoT.

El firmware también implementa relay LoRa, diagnóstico Modbus, salud y recuperación de la radio, configuración por USB y LoRa, lectura remota de configuración, actualización de firmware y reversión de configuraciones o imágenes que no vuelven a registrarse.

No está implementada la ejecución del catálogo `writes[]` ni el canal general de comandos MQTT. El register de un nodo normal a través de un supernodo también sigue pendiente.

## Configuración

El archivo operativo es `/config.json` en LittleFS. Las configuraciones de `configs/` describen el banco y no contienen secretos. El formato completo se define en [`../shared/protocol/node-config.md`](../shared/protocol/node-config.md).

Una configuración puede cargarse desde el visor por Web Serial o mediante el canal LoRa. Antes de aplicarla se valida completa. Tras el reinicio queda a prueba hasta que el nodo vuelve a registrarse; si no lo consigue dentro de la ventana configurada, se restaura la anterior.

## Compilación y carga

La compilación y la carga se realizan desde VS Code:

1. `Cmd+Shift+P`, `PlatformIO: Build`.
2. `Cmd+Shift+P`, `PlatformIO: Upload`.
3. `Cmd+Shift+P`, `PlatformIO: Monitor` para observar un dispositivo.

El banco dispone de un solo monitor serie USB, por lo que las capturas de dos nodos se realizan por separado.

## Artefactos de distribución

Después de compilar en VS Code, `make_dist.sh` genera los binarios que consume el gateway para aprovisionamiento USB y actualización por radio. El empaquetado no sustituye la compilación.

## Documentos relacionados

La arquitectura general está en [`../ARCHITECTURE.md`](../ARCHITECTURE.md). La trama LoRa se define en [`../shared/protocol/frame-format.md`](../shared/protocol/frame-format.md), los mensajes MQTT en [`../shared/protocol/batch-format.md`](../shared/protocol/batch-format.md) y el control de acceso al medio en [`../shared/protocol/mac.md`](../shared/protocol/mac.md).
