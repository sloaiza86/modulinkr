# Recuperación LoRa del nodo

El firmware del Atom, el firmware interno del RAK3172-E y la preparación de la radio antes de una instalación OTA intervienen en la recuperación del nodo. La versión ModuLinkr validada en este banco es 0.0.84+741aab543a96 y la versión interna del RAK es RUI_4.3.0_RAK3172-E.

## Diagnóstico y corrección

El registro de 0.0.83 muestra transmisiones y ACK hasta que faltan confirmaciones locales de envío. La recuperación termina en un reinicio automático del ESP32 a las 17:52:46 UTC del 2026-10-14. El arranque posterior no recupera la respuesta AT, aunque siguen apareciendo tramas recibidas por LoRa. Reiniciar el Atom no equivale a cortar la alimentación del módulo externo.

El fabricante documenta RUI-1044, corregido en RUI 4.2.0: el RAK3172 controlado por ESP32 puede dejar de responder tras un pulso de 10 microsegundos en RX de UART2. La versión 4.0.6 observada en el banco precede a esa corrección. La comparación de `uhal_uart.c` entre los tags 4.0.6_251 y 4.2.0_294 confirma que se añadió recuperación de la recepción UART mediante el callback de error de HAL. El síntoma es compatible con ese defecto; no se ha medido el pulso eléctrico en el banco. Se utiliza la imagen oficial 4.3.0, que incluye esa corrección. La referencia del fabricante es su [registro de cambios](https://downloads.rakwireless.com/RUI/RUI3/Image/CHANGELOG.md).

En ModuLinkr se aumenta a 4096 bytes el buffer UART de radio, se espera silencio antes de cada comando y se comprueba el modo actual antes de cambiarlo. Se detiene la recepción y se desactiva LPM antes de aplicar parámetros. Un fallo de sincronización añade sondeos con escritura espaciada. ATZ queda limitado a un intento por cinco minutos, con comprobación de respuesta posterior. Un arranque saludable y la preparación OTA no requieren ATZ.

La ausencia de `TXP2P DONE` deja incierto el resultado físico del envío. Se detiene la cola para evitar que una confirmación tardía se atribuya al siguiente paquete. La recuperación solo reabre el envío tras reconfigurar la radio; la declaración de recuperación en el monitor requiere observar tráfico nuevo. Un fallo de inicialización establece la radio como no disponible y utiliza el reintento de arranque de treinta segundos. Se retira el reinicio automático del Atom por escalada de salud LoRa, conservando su hora y la outbox.

La instalación OTA comprueba primero tamaño, hash y estado de la imagen. Después espera la guarda física de transmisión, detiene RX y exige respuesta AT. Si falla esta preparación, no cambia la partición de arranque ni reinicia el Atom. Conserva la imagen y su mapa, devuelve resultado 6 y persiste FW_REPORT con causa 18, `RADIO_NOT_READY`. El gateway cierra ese intento y el visor indica que la radio no quedó preparada y que la imagen sigue recibida. Una recuperación posterior permite un nuevo intento normal de instalación.

## Actualización interna del RAK por USB

El entorno `rak-bridge` conecta el USB del Atom con los GPIO 19 y 22 ya utilizados por el RAK. No accede a NVS ni a LittleFS y no incorpora hardware adicional. Se utiliza temporalmente; después debe restaurarse el entorno normal `atom-lite`. El puente de mantenimiento requiere acceso USB y no constituye un mecanismo de actualización remota del RAK.

El cargador verifica el SHA-256 y la variante E antes de entrar en el cargador de arranque. Comprueba también los vectores STM32 de la imagen. Se utiliza el transporte YMODEM del paquete oficial RAK, verificando su código y manteniendo una única apertura del puerto. Las respuestas `OK` se aceptan como líneas completas, también cuando no llevan una línea vacía anterior. La transferencia termina solo después de consultar y confirmar la versión instalada.

| Artefacto oficial | Identidad fijada |
| --- | --- |
| Aplicación RAK3172-E | RUI_4.3.0_RAK3172-E, 190784 bytes |
| SHA-256 de aplicación | `a9937177659e26d0f8c6483d9a58f46f3c84ee66be00b53866addc421325df21` |
| SHA-256 de `uploader_ymodem.py` | `660552428f635746ecb5978d5488b5eb2bc2c81c70c61790c279dfda6ed4413a` |

La URL oficial de la aplicación termina en `latest.bin`. Si cambia su contenido, el cargador rechaza el hash fijado y no escribe firmware. No se descarga ni se distribuye una imagen de otra variante.

En el Mac se selecciona `env:rak-bridge (nodo)` en el selector PlatformIO de VS Code, se ejecuta Build y después Upload. Se cierra el monitor serie antes de utilizar el puerto. El comando completo del cargador es:

```bash
"/Users/santiago/.platformio/penv/bin/python" "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/nodo/tools/update_rak.py" --port /dev/cu.usbserial-7156BE0838
```

El comando descarga y verifica los dos archivos oficiales. `--image` y `--tool` permiten usar los archivos verificados ya descargados. Si una transferencia se interrumpe después de identificar correctamente ese RAK3172-E, se conserva el puente y puede reanudarse añadiendo `--resume-boot`; este modo exige la respuesta del cargador de arranque y no sirve para identificar por primera vez un módulo desconocido.

Tras confirmar `Radio instalada: RUI_4.3.0_RAK3172-E`, se selecciona `Default (nodo)` o `env:atom-lite (nodo)` en VS Code y se ejecutan Build, Upload y Monitor. El módulo LoRa conserva su firmware interno al restaurar la aplicación del Atom. Todos los equipos destinados al despliegue deben comprobar la versión interna del RAK durante su preparación inicial. ModuLinkr continúa antes de su primera publicación pública.

## Validación del banco

Se compiló y cargó el puente desde VS Code y se actualizó el RAK3172-E por `/dev/cu.usbserial-7156BE0838`. El cargador confirmó 4.0.6 antes de la transferencia y 4.3.0 después. Se restauró ModuLinkr 0.0.84+741aab543a96 y se repitió Upload de la misma imagen sin desconectar el USB. Después de ambas cargas se observaron medidas Modbus, transmisión local y ACK del gateway. La fuente de 12 V del sensor estaba desconectada en la primera comprobación de Modbus; al conectarla volvieron las lecturas.

Se superan 67 pruebas del nodo con C++ nativo y dependencias simuladas, 108 del servicio gateway, 32 de identidad de firmware del visor y 16 JavaScript de interfaz OTA. Las pruebas incluyen cien arranques simulados, UART silenciosa, comandos rechazados, límites de recuperación, confirmaciones tardías, descarte de líneas demasiado largas, preparación OTA fallida sin cambiar partición, conservación de imagen y validación del puente binario.

La carga USB y la comunicación física están comprobadas en el nodo conectado. La duración observada del banco no establece una garantía de estabilidad indefinida. Permanecen pendientes la instalación OTA completa por radio con esta versión, la actualización interna de otros RAK del banco y la validación de estas condiciones en el supernodo con NB-IoT activo. Se instaló en el gateway el paquete que incorpora causa 18 y los binarios 0.0.84. El instalador confirmó tres respuestas HTTP de login desde el PID 7205 y ambos servicios activos. Después del reinicio del gateway se observaron ACK nuevos del nodo. A las 20:44:19 UTC se habían confirmado 48 muestras desde la última carga USB; el contador de timeouts era cero. Se observaron errores aislados de transmisión recuperados y no se presenta esta comprobación como ausencia de todo error de radio.

## Paquete del gateway

El paquete instalado es `/private/tmp/modulinkr-radio-recovery-gateway.tar.gz`, con 33 archivos y SHA-256 `afdcb84b0f699a27889f9899ba61be90692c106ebecdd97fb2ae4c7dd4c61ed5`. Incluye la aplicación de 608720 bytes con SHA-256 `7a630c7707d91b2eea8fc319b081c31724aee1daa7a103ccb9b9c75381eaf550`. El paquete se transfirió mediante la sesión Termius autenticada y se comprobó su hash antes de ejecutar el instalador. No se utilizaron paquetes anteriores.

Para reproducir el empaquetado desde el Mac, después de Build y `make_dist.sh`:

```bash
python3 "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/shared/protocol/package_identity.py" gateway /private/tmp/modulinkr-radio-recovery-gateway.tar.gz && scp /private/tmp/modulinkr-radio-recovery-gateway.tar.gz modulinkr@Gateway.local:/tmp/modulinkr-radio-recovery-gateway.tar.gz
```

El `&&` impide copiar un archivo anterior si falla el empaquetado. En la sesión Termius del gateway, el procedimiento de instalación completa y comprobación es:

```bash
mkdir -p /tmp/modulinkr-radio-recovery-gateway && tar -xzf /tmp/modulinkr-radio-recovery-gateway.tar.gz -C /tmp/modulinkr-radio-recovery-gateway && bash /tmp/modulinkr-radio-recovery-gateway/gateway/pi-service/install_identity.sh
sudo systemctl status modulinkr-gateway.service modulinkr-web.service --no-pager
```

## Archivos de esta corrección

En el repositorio se modifican `nodo/src/main.cpp`, `lora.h`, `lora.cpp`, `fwota.h`, `fwota.cpp`, `nodo/platformio.ini` y `nodo/README.md`. Se incorporan `nodo/src/rak_bridge.cpp`, `nodo/tools/update_rak.py`, `nodo/tests/test_radio_health.py`, `nodo/tests/test_rak_update.py` y este documento. Se actualizan las pruebas `test_radio_startup.py`, `test_ota_coexistence.py`, `test_firmware_transport.py` y `test_tx_ack_timing.py` del nodo.

En el gateway se modifican `pi-service/protocol.py`, `pi-service/gateway_service.py`, `pi-web/otaapi.py`, `pi-web/static/app.js` y `pi-web/static/index.html`, junto con `pi-service/tests/test_firmware_install_check.py`, `pi-web/tests/test_firmware_identity.py` y `pi-web/tests/test_firmware_ui.cjs`. Se actualiza `shared/protocol/frame-format.md`. La distribución regenerada contiene `pi-service/nodo.bin`, `nodo.bin.version`, `nodo-app.bin`, `nodo-app.bin.sha256` y `nodo-app.bin.version`.

Fuera del repositorio se añade la entrada técnica correspondiente a `TFM/bitacora.md`. Las imágenes oficiales descargadas, el cargador RAK y el paquete temporal del gateway permanecen en `/private/tmp` y no forman parte de los archivos del proyecto.
