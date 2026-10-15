# Alta USB y firmware del RAK

El firmware común del nodo, la API del gateway y el navegador realizan la preparación del RAK3172-E durante el alta USB. El mantenimiento queda incluido en ModuLinkr 0.0.85 y utiliza el USB del Atom y la UART ya conectada al módulo. No requiere componentes adicionales ni sustituir temporalmente ModuLinkr por un puente.

## Política de versiones

La fuente de verdad es [`../../gateway/pi-service/rak_policy.json`](../../gateway/pi-service/rak_policy.json). El script de precompilación genera las constantes C++ bajo `.pio/generated`. El gateway y el cargador de mantenimiento leen la misma política; el navegador la obtiene de la API autenticada. Una modificación de las versiones aprobadas exige recompilar el firmware y desplegar la política correspondiente.

| Versión identificada | Tratamiento |
| --- | --- |
| Aprobada explícitamente, incluida una posterior al destino | Se conserva y se permite continuar |
| Anterior al destino y dentro del intervalo admitido | Se actualiza a la imagen aprobada antes de guardar la configuración |
| Posterior al destino y ausente de la lista aprobada | Se bloquea el alta sin escritura ni downgrade automático |
| Modelo distinto, versión demasiado antigua o respuesta desconocida | Se bloquea el alta con el motivo correspondiente |
| Cargador pendiente de una actualización identificada previamente en el mismo Atom | Se permite repetir el intento tras confirmar el cargador |

La política inicial aprueba RUI 4.3.0 para RAK3172-E y permite actualizar versiones anteriores desde 4.0.6. El mayor número de versión no acredita compatibilidad. Una versión posterior puede cambiar comandos o comportamiento; se incorpora a la lista aprobada después de validarla. El downgrade no forma parte del alta automática ni se garantiza para cualquier combinación de aplicación y cargador.

La aplicación oficial tiene 190784 bytes y SHA256 `a9937177659e26d0f8c6483d9a58f46f3c84ee66be00b53866addc421325df21`. La descarga utiliza la URL oficial fijada en la política. Si el fabricante modifica `latest.bin`, el hash deja de coincidir y se rechaza la imagen. La caché operativa se ubica fuera del repositorio, bajo la cuenta del visor, en `.cache/modulinkr/rak`; `MODULINKR_RAK_CACHE` permite cambiar esa ubicación. No se versiona el binario del fabricante.

## Alta y comprobación

El asistente identifica el nodo y muestra por separado el firmware ModuLinkr y el firmware de la radio RAK. Al guardar por USB, instala primero ModuLinkr si el Atom aún lo necesita, comprueba la radio y actualiza una versión anterior admitida. Solo después envía la configuración. Se conserva NVS y LittleFS durante la instalación del anfitrión. El navegador comprueba el chip ESP32 antes de escribir; el gateway utiliza el cargador limitado al mismo chip y excluye el puerto de su radio Heltec.

El progreso identifica descarga, copia al Atom, escritura del módulo y comprobación de versión. Los porcentajes de copia proceden de bloques confirmados por el Atom; los de instalación proceden de ACK del receptor YMODEM. La última confirmación requiere consultar `AT+VER=?`. Una recepción completa de bytes no se presenta por sí sola como instalación completada.

En Actualizar firmware de nodos, la ficha USB incluye la versión RAK y la acción Comprobar y preparar radio RAK. Una radio aprobada no descarga ni escribe imagen. Una versión posterior desconocida muestra el bloqueo y su causa. La consulta por USB puede interrumpir brevemente la actividad de la radio mientras se detiene RX y se vuelve a configurar.

La API y el firmware comprueban estas condiciones aunque una petición omita la GUI. El cliente vincula la operación a la identidad física del Atom. La configuración se rechaza si el firmware no anuncia mantenimiento RAK o si la radio no está aprobada. El arranque normal tampoco habilita una radio cuya versión no figure entre las aprobadas.

La tabla de actualización LoRa conserva la versión anunciada por el catálogo en la columna En ejecución. La operación antigua se identifica como Última instalación por LoRa o Último intento por LoRa e incluye la fecha de su último cambio. Una operación activa se presenta como tal. La actualización USB del anfitrión no reemplaza ese historial ni permite atribuir a su versión antigua el estado actual del nodo.

## Conservación y recuperación

El nodo recibe bloques de hasta 1024 bytes en `/rak-update.part`. Comprueba tamaño, SHA256 y vectores STM32 antes de seleccionar `/rak-update.bin`. La falta de espacio rechaza la operación sin borrar configuración, outbox ni otros datos. Una recepción USB abandonada se cancela después de 30 s sin nuevos bloques, antes de entrar en BOOT.

Antes de entrar en BOOT se guarda `/rak-update.state` con la identidad física del Atom y la versión identificada. Una interrupción posterior conserva imagen y marcador. Al volver a conectar, la preparación comprueba el marcador y la respuesta del cargador antes de permitir otro intento. Un marcador anterior no autoriza escribir sobre una versión posterior desconocida o un modelo distinto. La versión se vuelve a consultar justo antes de iniciar la escritura.

Una vez iniciada la escritura, el Atom ejecuta YMODEM sin depender de que siga abierta la página. Una pérdida de alimentación puede dejar el módulo en su cargador; el siguiente intento requiere acceso USB al mismo Atom. Este mantenimiento no constituye una actualización remota del RAK por LoRa ni permite garantizar la recuperación por software de cualquier fallo físico del módulo.

## Validación y límites

Las pruebas nativas ejecutan el mantenimiento C++ con UART, almacenamiento y hash controlados. Comprueban integridad de bloques y CRC16, reintentos, fallo e interrupción de transferencia, identidad del marcador, imagen inválida, espacio insuficiente, abandono USB, cambio de versión antes de escribir y rechazo de una versión posterior incluso con un marcador anterior. Se verifica que configuración y outbox conserven su contenido. El hash de estas pruebas utiliza una implementación controlada; la aplicación ESP32 utiliza mbedTLS.

Las pruebas Python y JavaScript ejecutan los clientes de transferencia, la política, la caché verificada, la identidad del equipo, el rechazo de posiciones incorrectas y la confirmación final. La API se prueba también con peticiones directas que intentan omitir las barreras de la interfaz.

Se ejecutan 83 pruebas del nodo, 120 del servicio gateway y 404 del visor, con una omitida en el visor. Las 104 pruebas JavaScript y de navegador se superan. El Build final de PlatformIO utiliza 56580 bytes de RAM y 617337 bytes de flash; la imagen de aplicación ocupa 617696 bytes. El Build y las cargas USB se realizan desde VS Code.

En el nodo 1 del banco se cargó 0.0.85 desde PlatformIO de VS Code. `CFG.HELLO` confirmó la identidad `f8b3b71bdf58`, configuración presente y RUI_4.3.0_RAK3172-E aprobada. `CFG.RAK.INFO` consultó el módulo y reanudó la radio. Se observaron lecturas Modbus y ACK posteriores. La actualización física anterior de 4.0.6 a 4.3.0 utilizó el puente de mantenimiento, como se registra en [`radio-recovery.md`](radio-recovery.md).

En el supernodo 2 se confirmó por USB la identidad `f8b3b71bdfa4` y ModuLinkr 0.0.83 antes de cargar el firmware común 0.0.85. La GUI identificó RUI_4.0.6_RAK3172-E y ejecutó Comprobar y preparar radio RAK desde Este equipo. El nuevo emisor integrado completó la escritura YMODEM y la consulta final; la ficha mostró RUI_4.3.0_RAK3172-E y conservó el nombre, rol e identidad del supernodo. Después se observaron ACK nuevos, lecturas del acelerómetro con contador de errores cero y MQTT conectado. A las 06:57:03 UTC se habían confirmado 17 muestras desde ese arranque. Esta observación no establece estabilidad indefinida. La recuperación física tras cortar una transferencia y el alta inicial de un Atom sin ModuLinkr permanecen pendientes; sus barreras y estados se prueban con dependencias simuladas.

La observación posterior alcanza 41 muestras confirmadas a las 07:01:16 UTC, con lecturas Modbus correctas. A las 07:01:22 UTC el módem anuncia desconexión MQTT y a las 07:01:33 UTC registra de nuevo `nbiot.mqtt_ready`. Esta recuperación celular se distingue de la actualización del RAK y no se presenta la prueba como ausencia de desconexiones de red.

La escritura física se realiza con el USB conectado al Mac y el cliente Web Serial del navegador. El cliente del USB del gateway se comprueba con transporte simulado y pruebas de API; no se conecta un Atom al Pi durante esta validación.

| Equipo del banco | Puerto del Mac | Identidad física |
| --- | --- | --- |
| Nodo 1 | `/dev/cu.usbserial-7156BE0838` | `f8b3b71bdf58` |
| Supernodo 2 | `/dev/cu.usbserial-8152BD2638` | `f8b3b71bdfa4` |

La distribución final es 0.0.85+cef374a1323e, con aplicación de 617696 bytes y SHA256 `dad77e170574456da56d9053d7efbf40ddccbfd0c276df94b14798ad60f0f5f3`. El paquete del gateway contiene 37 archivos y SHA256 `8a04513d279bbe12fb254fcaa1ce01f99d2ce34af7be1f2f3b0f0ec0b71e5f51`. Se verificó antes de instalar. El instalador confirmó ambos servicios activos y tres respuestas HTTP desde el mismo PID 2528. Se descargó también la imagen oficial del RAK en el gateway y se verificaron sus 190784 bytes y su hash fijado.

## Distribución

Después de Build en VS Code, en el Mac:

```bash
cd "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/nodo" && ./make_dist.sh
python3 "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/shared/protocol/package_identity.py" gateway /private/tmp/modulinkr-rak-onboarding-gateway.tar.gz && scp /private/tmp/modulinkr-rak-onboarding-gateway.tar.gz modulinkr@Gateway.local:/tmp/modulinkr-rak-onboarding-gateway.tar.gz
```

En la sesión Termius del gateway:

```bash
mkdir -p /tmp/modulinkr-rak-onboarding-gateway && tar -xzf /tmp/modulinkr-rak-onboarding-gateway.tar.gz -C /tmp/modulinkr-rak-onboarding-gateway && bash /tmp/modulinkr-rak-onboarding-gateway/gateway/pi-service/install_identity.sh
sudo systemctl status modulinkr-gateway.service modulinkr-web.service --no-pager
```

## Archivos de la implementación

En `nodo/` se modifican `platformio.ini`, `README.md`, `src/main.cpp`, `src/lora.cpp`, `src/commission.cpp`, `src/commission.h`, `tools/update_rak.py`, `tests/test_radio_startup.py`, `tests/test_ota_coexistence.py` y `tests/test_rak_update.py`. Se incorporan `src/rak_version.h`, `src/rak_maintenance.h`, `src/rak_maintenance.cpp`, `tools/generate_rak_policy.py` y `tests/test_rak_maintenance.py`.

En `gateway/pi-service/` se incorporan `rak_policy.json`, `rak_firmware.py`, `rak_commission.py`, `tests/test_rak_policy.py` y `tests/test_rak_commission.py`. Se modifica `install_identity.sh`. Se regeneran los cinco artefactos de distribución `nodo.bin`, `nodo.bin.version`, `nodo-app.bin`, `nodo-app.bin.sha256` y `nodo-app.bin.version`.

En `gateway/pi-web/` se modifican `configapi.py`, `firmwaremeta.py`, `static/app.js`, `static/index.html`, `tests/test_prepublic_package.py` y `tests/test_firmware_ui.cjs`. Se incorporan `static/rak-onboarding.js`, `tests/test_rak_onboarding.cjs`, `tests/test_rak_assistant.cjs` y `tests/test_rak_config_api.py`.

En `shared/protocol/` se incorpora este documento y se actualiza `radio-recovery.md`. Fuera del repositorio se añade la entrada técnica de `TFM/bitacora.md`. El encabezado generado bajo `.pio`, los paquetes temporales y la caché del fabricante no forman parte de los archivos del proyecto.
