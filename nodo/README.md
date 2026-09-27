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

## Indicador LED de estado

Desde la versión 0.0.66, el LED representa la vía utilizada y su confirmación. El verde fijo requiere un ACK del gateway, incluso con relays LoRa intermedios. El amarillo fijo requiere un ACK de custodia del supernodo: confirma la aceptación para salida por NB-IoT, no la llegada al servidor. El cian fijo requiere una publicación MQTT satisfactoria del supernodo. Cuando existe padre LoRa se prioriza esa vía; tener el módem celular preparado no cambia por sí solo el color a cian.

El arranque utiliza blanco tenue. La espera sin vía seleccionada o sin hora utiliza azul intermitente. Una vía seleccionada sin confirmación utiliza su color intermitente, con un segundo encendido y otro apagado. Un reintento LoRa aislado conserva la confirmación; agotarlos, perder el padre o cambiar de destino la invalida. La confirmación caduca después de tres intervalos de muestreo más 60 segundos, para no marcar como fallo los intervalos largos configurados. Una nueva confirmación recupera el color fijo.

Sin vía seleccionada, tres búsquedas de supernodo sin ofertas o tres entradas consecutivas en recuperación celular activan rojo intermitente. Una publicación satisfactoria reinicia el contador celular. La ausencia o invalidez de configuración y un fallo de inicialización sin alternativa utilizable producen rojo fijo. Estas señales no detienen los mecanismos existentes de recuperación.

Tres ciclos consecutivos con errores Modbus activan dos destellos rojos de 150 ms, separados por 150 ms, cada seis segundos sobre el color de comunicación. Un ciclo sin errores retira esta indicación. El rojo de fallo y el violeta de actualización tienen prioridad. Se utiliza violeta intermitente mientras hay actividad reciente de recepción de firmware; después de 30 segundos sin actividad se recupera la indicación de comunicación. Una transferencia guardada para reanudar no mantiene indefinidamente el violeta. La instalación confirmada muestra violeta antes de reiniciar. La escritura USB realizada por el cargador de arranque queda fuera del control del firmware.

Los patrones se actualizan desde el bucle principal sin añadir esperas. Una operación bloqueante existente puede alargar un pulso. El brillo se mantiene bajo. La prueba `tests/test_status_led.py` ejecuta la selección de colores y la caducidad de confirmaciones en C++ nativo; no sustituye la compilación ESP32 ni la comprobación visual en los dispositivos.

## Errores NB-IoT por serial

Desde 0.0.67, los fallos de comandos AT y operaciones NB-IoT se imprimen como `ERROR` por `Serial`, accesible mediante el USB del Atom. No dependen de `nbiot.debug` ni del volcado AT verboso. No se incorpora transmisión LoRa, almacenamiento de errores ni campos nuevos en las muestras.

El evento `nbiot.modem_error` identifica la etapa y muestra la respuesta recibida. Las respuestas extensas se dividen en partes de hasta 160 bytes para evitar el truncamiento del registro general. Se omiten las líneas de eco que empiezan por AT, que pueden contener credenciales, y el formateador general normaliza caracteres de control. Una respuesta vacía se representa como `no_response`. La espera del prompt de topic o payload conserva los bytes recibidos, incluidos códigos de error, en lugar de sustituirlos por un texto genérico de timeout. Las consultas posteriores no modifican el error ya impreso.

Se incluyen los fallos de SIM, APN, registro, inicio y reconexión MQTT, publicación y sincronización NTP. Un comando rechazado durante una limpieza o una opción tolerada también puede producir un registro de error sin detener la recuperación existente. La salida permite observar eventos mientras el monitor serie está conectado; no se recuperan posteriormente los eventos que no se capturaron. No equivale a un volcado continuo de la UART del módem.
