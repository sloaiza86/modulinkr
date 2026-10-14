# ModuLinkr, firmware de nodo y supernodo

El directorio contiene el firmware común de los dispositivos de campo ModuLinkr.

## Estado actual

La versión declarada en `src/main.cpp` es `0.0.84`. El firmware exige identidad física, protocolo LoRa 4.0 y configuración 3.3. Las configuraciones de banco `configs/nodo1.json` y `configs/nodo2.json` usan `3.3`. ModuLinkr permanece antes de su primera publicación; los formatos internos previos no constituyen versiones desplegadas en clientes.

El rol no se decide al compilar. `node.type` y los demás campos de `/config.json` determinan si el equipo opera como nodo o supernodo, qué dispositivo Modbus lee y qué parámetros de red utiliza.

## Hardware del banco

| Rol | Componentes |
| --- | --- |
| Nodo | M5Stack Atom Lite, Atom DTU LoRaWAN EU868 y XY-MD02 |
| Supernodo | M5Stack Atom Lite, Atom DTU LoRaWAN EU868, NB-IoT 2 Unit SIM7028 y WT901C485 |

El Atom Lite usa SoftwareSerial para Modbus y reserva las UART hardware para LoRa y NB-IoT. El bloque NB-IoT solo se inicia cuando la configuración declara el rol de supernodo.

## Comportamiento

El dispositivo obtiene hora antes de muestrear, se registra en la red y anuncia su catálogo. La telemetría incluye timestamp, valores y estado de cada transacción Modbus. Las muestras no confirmadas permanecen en la outbox y pueden entregarse mediante custodia NB-IoT.

Desde 0.0.80, cada fragmento del catálogo contiene como máximo 219 bytes. Los dos bytes de índice y total completan el payload de 221 bytes admitido con AES-CCM. Se utiliza el mismo tamaño sin cifrado para mantener una fragmentación común. Un catálogo de 429 bytes se envía en fragmentos de 219 y 210 bytes; el gateway reensambla por índice y responde WELCOME al completar el catálogo. La prueba local recorre constructor, envío, cifrado AES-CCM mediante un puente de prueba en Python, decodificador y manejador de registro del gateway. La UART queda simulada; permanece pendiente la validación física.

Desde 0.0.78, el primer muestreo se realiza en cuanto se permite y el siguiente espera un intervalo completo desde el final de la captura anterior. El tiempo bloqueado no genera una ráfaga de capturas atrasadas. Los valores y timestamps de muestras ya tomadas se conservan al retransmitir.

Una trama aceptada por el driver puede estar todavía en cola. La espera del ACK empieza al recibir `TXP2P DONE`, que confirma la transmisión local del módulo. En ese momento se reserva también la guarda de recepción de 500 ms cuando el ACK está habilitado. Se aplica a telemetría propia nueva, reintentos, drenaje de outbox y custodia; las tramas ajenas retransmitidas como relay no activan el temporizador de muestras propias. La guarda no acorta una retención OTA existente.

El evento `lora.tx_complete` identifica secuencia, destino, resultado local y plazo de ACK. `result=sent` no confirma entrega al gateway: esa confirmación sigue siendo `lora.ack`. Un error, el agotamiento de CAD, la ausencia de DONE o el descarte durante una recuperación liberan la espera local para reintentar o retener la muestra. Una cola llena aplaza el reintento sin consumir su presupuesto y una muestra nueva rechazada se guarda en la outbox. `lora.tx_ok` conserva su nombre y representa aceptación por el driver, incluida la cola.

El firmware también implementa relay LoRa, diagnóstico Modbus, salud y recuperación de la radio, configuración por USB y LoRa, lectura remota de configuración, actualización de firmware y reversión de configuraciones o imágenes que no vuelven a registrarse.

Se implementa la ejecución remota de `writes[]` para 0x05, 0x0F, 0x06 y 0x10, con LoRa principal y NB-IoT de respaldo. La recepción MQTT corresponde a escrituras, sin habilitar los demás comandos del diseño histórico. El register de un nodo normal a través de un supernodo sigue pendiente. Se supera PlatformIO Build para ESP32 desde VS Code en 0.0.80. La carga del firmware corregido y el banco físico del control permanecen pendientes.

## Configuración

El archivo operativo es `/config.json` en LittleFS. Las configuraciones de `configs/` describen el banco y las pruebas de escritura, documentadas en [`configs/README.md`](configs/README.md). El formato completo se define en [`../shared/protocol/node-config.md`](../shared/protocol/node-config.md).

Una configuración puede cargarse desde el visor por Web Serial o mediante el canal LoRa. Antes de aplicarla se valida completa. Tras el reinicio queda a prueba hasta que el nodo vuelve a registrarse; si no lo consigue dentro de la ventana configurada, se restaura la anterior.

## Compilación y carga

La compilación y la carga se realizan desde VS Code:

1. `Cmd+Shift+P`, `PlatformIO: Build`.
2. `Cmd+Shift+P`, `PlatformIO: Upload`.
3. `Cmd+Shift+P`, `PlatformIO: Monitor` para observar un dispositivo.

El banco dispone de un solo monitor serie USB, por lo que las capturas de dos nodos se realizan por separado.

## Artefactos de distribución

Después de compilar en VS Code, `make_dist.sh` genera los binarios que consume el gateway para aprovisionamiento USB y actualización por radio. El empaquetado no sustituye la compilación.

## Recuperación LoRa tras un reinicio

El Atom y el módulo LoRa se reinician por separado. El arranque de 0.0.84 sincroniza la UART, detiene RX, comprueba el modo P2P y desactiva el sueño del RAK antes de configurar la radio. No se envía ATZ en un arranque saludable ni se cambia de modo cuando el módulo ya está en P2P. Un sondeo fallido añade una escritura espaciada y limita los intentos de ATZ a uno cada cinco minutos. La falta de inicialización mantiene la radio no disponible y reintenta después de treinta segundos.

La ausencia de `TXP2P DONE` detiene las transmisiones posteriores hasta recuperar la radio. Se evita atribuir una confirmación tardía al siguiente envío. La recuperación requiere observar tráfico nuevo; una configuración aceptada por AT no acredita por sí sola una transmisión. La recuperación de radio no reinicia automáticamente el Atom ni elimina su hora o la outbox.

La instalación OTA verifica la imagen antes de detener RX y confirmar el sondeo final. Si no se confirma la preparación, no se selecciona la partición nueva ni se reinicia: se conserva la imagen recibida y se comunica `RADIO_NOT_READY`, causa 18. El gateway y el visor muestran que la instalación se detuvo porque la radio no quedó preparada y que la imagen continúa disponible.

El RAK3172-E del banco se actualizó de RUI 4.0.6 a RUI 4.3.0 mediante el USB del Atom. Esta versión incluye la corrección del fabricante para el bloqueo de UART2 con un ESP32 anfitrión. Se validaron la restauración de ModuLinkr y una carga USB posterior, sin desconexión total, con lecturas Modbus y ACK del gateway. La actualización interna del RAK se realiza mediante el entorno temporal `rak-bridge`; después se restaura `atom-lite`. El procedimiento, las pruebas y sus límites figuran en [`../shared/protocol/radio-recovery.md`](../shared/protocol/radio-recovery.md).

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

## Mantenimiento remoto

El firmware 0.0.68 admite reinicio y puesta a cero de contadores mediante LoRa desde Configuración, Mantenimiento, Nodos. Se conserva la configuración y la outbox. El reinicio se rechaza mientras existan muestras pendientes de ACK o una operación de firmware o configuración incompatible. Tras aceptar la orden se reserva una espera de tres segundos para responder antes de reiniciar.

La puesta a cero afecta a arranques, consultas a la radio sin respuesta, reconfiguraciones de radio, reinicios de radio y reinicios automáticos del nodo. Conserva el último fallo y el motivo del último arranque. Se guarda la fecha de puesta a cero junto con los contadores. La operación persistida permite contestar a reintentos sin volver a ejecutarla, también después del arranque. La confirmación de reinicio requiere una respuesta del firmware posterior al arranque.

Se requiere actualizar el servicio del gateway, el nodo y los relays del recorrido. Los nodos conectados exclusivamente por NB-IoT no admiten estas acciones. `tests/test_node_maintenance.py` ejecuta el manejador con reloj, radio y persistencia simulados en C++ nativo. No sustituye la compilación ESP32 ni las pruebas con radio real. El formato y los estados se describen en §24 de [`frame-format.md`](../shared/protocol/frame-format.md).


## Escrituras remotas

Las acciones declaradas en `writes[]` se reciben por LoRa o por el servicio NB-IoT del supernodo. El bucle principal ejecuta la transacción Modbus en una ventana sin transmisiones propias ni ACK pendientes, separado de la tarea del módem. Se guarda el identificador y la huella antes de tocar el bus, y después el resultado. El archivo operativo `/write-command.bin` contiene el último resultado; no contiene credenciales.

El catálogo de registro anuncia la capacidad y la definición de cada acción. Los relays del recorrido también necesitan 0.0.79 o una implementación compatible. La pérdida de una respuesta Modbus no autoriza repetir la escritura: se devuelve incertidumbre y un duplicado solo recupera el resultado persistido. La especificación está en [`commands-format.md`](../shared/protocol/commands-format.md) §12 y [`frame-format.md`](../shared/protocol/frame-format.md) §26.

Desde 0.0.81, el catálogo relaciona cada operación con sus lecturas y admite presentación numérica, interruptores, selectores y acciones mediante `writes[].ui`. La relación automática exige posiciones y conversiones compatibles; los identificadores de lectura permiten una relación explícita. La extensión de catálogo requiere actualizar primero el gateway. El límite de fragmentación seguro permanece en 219 bytes y el protocolo de órdenes conserva las cuatro funciones anteriores.


## Identidad física y despliegues

El código del nodo 0.0.83 utiliza la radio 4.0, catálogos por huella y muestras vinculadas a equipo y sesión. El gateway bloquea identificadores repetidos y el consumidor separa los despliegues. El contrato actual, la conservación interna del banco y el procedimiento de despliegue se describen en [`identity-safety.md`](../shared/protocol/identity-safety.md). El banco debe utilizar nodos y relays del contrato de radio 4.0; las versiones internas previas no representan clientes publicados.
