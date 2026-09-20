# ModuLinkr, visor web del gateway

El visor web local está implementado como servicio FastAPI independiente. Incluye estado y topología de red, consulta y exportación de datos, configuración del gateway, comisionamiento por USB del gateway o Web Serial, configuración remota, actualización de firmware por LoRa, diagnóstico y migración de parámetros de red. El módulo general de comandos Modbus continúa como stub porque el plano de comandos descrito en `commands-format.md` no está implementado de extremo a extremo.

La primera base funcional se completó el 2026-07-16. La fase 3.1 añadió el selector escalable, las vistas guardadas y los ejes automáticos por unidad. La vista de red muestra además el ciclo de trabajo de la última hora por transmisor, calculado por deltas de los reportes `tx_ms` de la tabla `node_airtime` (`frame-format.md` §6).

Arranque manual en banco (sin instalador todavía): `./get_vendor.sh` una vez con Internet (descarga las versiones fijadas de vis-network, ECharts, esptool-js, Cally y Web Awesome a `static/vendor/`), venv con `fastapi`, `uvicorn` y `psycopg2-binary`, y:

```
MODULINKR_DB=<ruta del buffer> \
MODULINKR_PG_HOST=<dominio de la VM> MODULINKR_PG_PASSWORD=<clave de modulinkr_ro> \
uvicorn web_service:app --host 0.0.0.0 --port 8080
```

Sin `MODULINKR_WEB_USER` el visor arranca sin autenticación y lo avisa en el log; sin `MODULINKR_PG_HOST` solo degrada el módulo de datos (503), el resto opera. El rol `modulinkr_ro` y el listener remoto los provisiona el componente `database` del instalador del servidor (`db_enable_remote_ro`); requiere además abrir 5432/tcp en el firewall de la nube. El cliente conecta con `sslmode=require`: canal cifrado, identidad del servidor no verificada (la protección es la contraseña; mejora futura: `verify-full` con el certificado del dominio).

## 1. Propósito y alcance

Interfaz web servida desde el Raspberry Pi del gateway y organizada en cuatro funciones:

1. **Datos**: gráficos de las medidas guardadas en la base cloud y export CSV, con selección de nodos, medidas y rango temporal.
2. **Estado de la red**: qué nodos están conectados, cuáles se vieron alguna vez y cuándo fue la última vez, y por qué ruta entregan (LoRa o NB-IoT).
3. **Comandos de escritura**: pospuesto. Queda como módulo stub hasta que el firmware de comandos madure (recepción en supernodo incompleta; downlink LoRa a nodos sin NB-IoT sin implementar, `frame-format.md` §11).
4. **Topología**: mapa del árbol mesh (gateway raíz, arista hijo a padre), al estilo del mapa de red de Zigbee2MQTT.

## 2. Decisiones de arquitectura (2026-07-16)

- **Corre en el Pi** (`modulinkr-web`, servicio systemd separado del gateway). El Pi es el dueño de la verdad viva de la red; las funciones 2 y 4 operan 100 % en local y sobreviven sin Internet. La función 1 consulta la base remota y degrada con aviso si no hay conexión.
- **Stack**: FastAPI + uvicorn (Python, coherente con el resto del proyecto), frontend estático servido por el propio backend, sin build ni Node: ECharts para gráficos, vis-network para el mapa. Presupuesto de memoria ~40-60 MB (los gráficos los renderiza el navegador del cliente; el Pi solo sirve JSON y estáticos).
- **Modularidad**: un router de API por función (`/api/datos`, `/api/red`, `/api/topologia`; futuro `/api/comandos`), cada uno con su vista. Añadir una función es añadir un módulo, sin tocar los demás.
- **Acceso al histórico**: PostgreSQL de la VM expuesto en 5432 con TLS y un rol **solo lectura** dedicado (`modulinkr_ro`, únicamente `SELECT` sobre la base de telemetría; `pg_hba` restringido a ese rol y base). Lo provisiona el instalador del servidor.
- **Autenticación**: página de login propia con cookie de sesión firmada (HMAC SHA-256 con `MODULINKR_WEB_SECRET`; usuario y contraseña preguntados en la instalación, la clave de firma autogenerada). Sustituye al basic auth de la primera iteración: misma protección, sin el diálogo del navegador y con logout. Desde el 2026-07-25 el visor sirve HTTPS con certificado autofirmado (§8); la cookie se marca `Secure` cuando hay TLS.

### 2.1 Componentes web (2026-08-13)

El frontend usa Custom Elements nativos registrados en `static/components.js`. La aplicación, la barra lateral, la cabecera, el enrutador, las vistas, las tarjetas de nodo, las mediciones, el detalle, el histórico, los diálogos, los avisos y la pantalla de acceso tienen límites de componente explícitos. Las tarjetas y mediciones creadas con telemetría se comunican con la aplicación mediante eventos del DOM, por lo que el refresco periódico sustituye su contenido sin volver a conectar manejadores de interacción.

Se mantiene el DOM ligero y la hoja de estilos compartida. Esta elección conserva las variables visuales de `style.css`, los identificadores usados por los formularios existentes y la compatibilidad con ECharts, vis-network, Web Serial y esptool-js. No se incorpora Lit ni un proceso de compilación: el visor debe seguir instalándose como archivos estáticos, funcionar sin Internet y consumir pocos recursos en la Raspberry Pi. Los componentes nativos permiten desarrollar el dashboard y sus tarjetas sobre la arquitectura definitiva sin imponer una migración posterior.

### 2.2 Sistema de iconos (2026-08-13)

La interfaz usa Material Design Icons 7.4.47 mediante el componente `<modulinkr-icon>`. Se incorpora el catálogo completo de 7.447 pictogramas porque no es posible anticipar todos los sensores, actuadores y equipos que podrán configurarse. El navegador no descarga el catálogo completo: `static/mdi/manifest.json` localiza el bloque alfabético que contiene cada nombre, el componente solicita ese bloque una sola vez y conserva sus iconos en memoria. Los 39 bloques suman aproximadamente 2,7 MB en el gateway y la pantalla solo transfiere los que utiliza. No se requiere Internet, Node ni un servicio externo durante la operación.

Todo icono de la interfaz se solicita con el espacio de nombres `mdi:`, por ejemplo `<modulinkr-icon name="mdi:thermometer">`. El componente constituye el único punto de renderizado para evitar SVG duplicados, mantener tamaño y color mediante CSS y permitir que un nombre pueda sustituirse de forma centralizada. Si el icono transmite información que el texto contiguo no expresa, se añade el atributo `label`; en caso contrario queda oculto para los lectores de pantalla como elemento decorativo.

Los logotipos de tecnologías y marcas no se fuerzan dentro de MDI. Cuando se incorporen LoRa, NB-IoT, Modbus u otras identidades oficiales, se reservarán espacios de nombres propios y se conservarán sus condiciones de uso. Los pictogramas específicos de ModuLinkr seguirán el mismo punto de acceso, pero no se mezclarán con el catálogo de terceros.

El catálogo se regenera con `tools/build_mdi_catalog.py` a partir del paquete oficial `@mdi/svg`. La versión, el número de iconos y la licencia quedan registrados en el manifiesto; el texto de licencia distribuido por Pictogrammers se conserva en `static/mdi/LICENSE`. Una actualización de MDI debe regenerar todos los bloques y comprobar los nombres usados por `index.html` y `app.js` antes de sustituir la versión anterior.

## 3. Fuentes de datos

| Función | Fuente | Notas |
| --- | --- | --- |
| Datos (gráficos, CSV) | PostgreSQL de la VM (`nodes`, `channels`, `samples`, `sample_values`) | Selector de nodos y medidas desde el catálogo zero-touch. Consultas de `db-schema.md` §5. Rangos largos con agregación en servidor (`date_trunc`, promedio por bucket) para no arrastrar la serie cruda hasta el navegador. CSV en streaming con la misma consulta. |
| Estado de red | `node_status`, `nbiot_last` y `gateway_status` del SQLite local | La suscripción MQTT del servicio registra las publicaciones celulares, incluso sin radio USB. Se distingue la actividad LoRa, la publicación del supernodo y la captura de cada muestra. |
| Topología | Estado de entrega compartido con las tarjetas | Se combina el padre LoRa observado con `nbiot_last.via_publisher`. La relación de entrega mediante un supernodo no confirma los saltos físicos intermedios. |
| Comandos (futuro) | Publicación MQTT a `modulinkr/v1/{node}/cmd` (`commands-format.md`) | Módulo stub, pendiente del soporte completo en firmware. |

## 4. Integración con el pi-service

1. Tabla `node_status` en `buffer.db`: una fila por nodo (`origin`, `last_seen`, `last_frame_type`, `rssi`, `snr`, `parent_id`, `hop_count`). La actualiza `gateway_service.py` con cada trama válida oída (también las overheard).
2. Cosecha de los ecos de BEACON para alimentar `parent_id` y `hop_count` de `node_status`.

La web abre `buffer.db` en modo solo lectura; no comparte proceso ni sockets con el gateway.

### Estado de entrega sin radio del gateway

Al desconectar el Heltec, `gateway_status.lora_link` invalida la ruta al gateway aunque todavía quede una observación LoRa dentro de su ventana de frescura. El servicio mantiene su suscripción a la telemetría MQTT. Una publicación reciente permite confirmar la entrega celular sin exigir que siga llegando el heartbeat LoRa del supernodo. Un diagnóstico posterior del módem prevalece sobre una publicación anterior para los indicadores NB-IoT y MQTT.

El nodo que entrega mediante un supernodo conserva su tipo. Su indicador LoRa aparece amarillo y muestra el identificador de salida. El mapa dibuja esa relación en amarillo punteado y la salida del supernodo hacia la red celular. No se presenta como un enlace físico directo, porque el mensaje identifica el origen y el publicador, pero no todos los saltos. Si vence la evidencia, el nodo y la última ruta pasan a gris. Una ruta LoRa observada, vigente y completa hasta el gateway tiene prioridad al recuperarse la radio. Los cambios de conexiones recalculan la disposición automática; las posiciones ajustadas manualmente se conservan.

El heartbeat LoRa y el diagnóstico del módem tienen una vigencia de 135 s, correspondiente a dos periodos de 60 s y 15 s de margen. La entrega celular de cada origen usa el mayor valor entre 45 s y dos periodos de muestreo más 15 s. El periodo se estima con al menos tres capturas distintas, usando la mediana de los intervalos de las últimas ocho capturas LoRa y celulares. Sin historial suficiente se usa una ventana provisional de 45 s; el arranque de un nodo de muestreo lento puede mostrar ausencia de actividad reciente hasta aprender su periodo. Un nodo que muestrea cada 600 s dispone de 1215 s cuando su cadencia ya se conoce. La actividad del publicador toma la menor ventana conocida entre los orígenes que entrega, o su propia ventana si aún no se conoce ninguna.

La captura y la recepción deben seguir dentro de la ventana del origen. Las muestras antiguas, futuras o duplicadas no rejuvenecen su estado. Una publicación nueva puede confirmar actividad del supernodo aunque contenga capturas antiguas; los mensajes retenidos y vacíos no sirven como evidencia actual. Se conserva la hora de recepción en el gateway al procesar la cola local. El servicio mantiene hasta ocho fechas de captura por origen en `nbiot_captures`, sin modificar el protocolo. La última medida se conserva visible con su antigüedad aunque caduque su frescura.

El navegador consulta `/api/red/resumen` cada 2 s. Este recurso lee estado, últimas medidas y catálogos en una misma transacción SQLite y no consulta la base cloud. Un reloj local actualiza los contadores y aplica los plazos de caducidad cada segundo; las tarjetas y el mapa solo se reconstruyen cuando cambia su contenido. Al ocultar la pestaña se suspende la consulta; al volver se actualiza inmediatamente. No se solapan peticiones ni se aceptan respuestas canceladas.

Sin nuevas publicaciones se indica ausencia de actividad reciente, no desconexión física confirmada. Si se pierde la conexión del gateway al broker, el estado celular queda sin observación, salvo diagnóstico vigente recibido por LoRa. Si el navegador pierde acceso al gateway, conserva la última información con su antigüedad y retira las confirmaciones actuales. La desconexión explícita del radio invalida su ruta en la siguiente consulta, sin esperar los 135 s.

El cambio no modifica `samples.source` ni la deduplicación cloud. Se conserva la vía de la primera inserción y no se añade un histórico de rutas por muestra. La comprobación local usa SQLite, estados simulados y el navegador; la desconexión y recuperación físicas de la radio siguen pendientes de validación en el gateway.


## 5. Estado de las fases

1. `node_status` y cosecha de topología en el pi-service: implementado.
2. Servicio FastAPI, login propio y módulos locales de red y topología: implementado.
3. Módulo de datos contra PostgreSQL remoto y exportación CSV: implementado.
4. Instalador del visor con venv, systemd y credenciales solicitadas de forma interactiva: implementado.
5. Módulo general de comandos: pendiente del soporte completo en firmware.

## 6. Configuración desde el visor (2026-07-24)

La vista Configuración adopta el patrón de ajustes de los paneles domóticos (items con icono, subtítulo y chevron; subrutas con volver) y reúne las páginas operativas del gateway:

**Cargar JSON vía USB** (`configapi.py`, `/api/config`): comisionamiento de un nodo Atom conectado por USB al Pi con el protocolo `CFG.*` del firmware (detección por sondeo de `CFG.HELLO`, lectura, carga con sha256 y borrado). El veredicto de validación es el del nodo; tras cargar o borrar, el visor re-detecta para confirmar el reinicio. El puerto del Heltec queda excluido de la búsqueda (`MODULINKR_GATEWAY_PORT` en `web.env`) y las operaciones serie van bajo un lock global.

En Añadir nodo y Editar nodo, la validación previa identifica el dispositivo,
la medida o la escritura y el campo que requiere corrección. Los límites de
texto se comprueban en bytes UTF-8: 16 para el nombre del dispositivo, 32 para
el nombre del nodo o de una medida o escritura, y 8 para identificadores y
unidades. Las tildes y otros símbolos pueden ocupar más de un byte. También
se detectan identificadores repetidos y los límites de 4 dispositivos,
8 medidas totales y 4 escrituras por dispositivo. Los rechazos conocidos del
nodo se presentan en castellano, incluidos los mensajes de versiones
anteriores. Un error ambiguo del firmware se explica como tal, sin atribuirlo
solo a un campo vacío. El diálogo de guardado distingue el rechazo de una
operación de los mensajes informativos.
Durante el progreso se conserva el indicador giratorio entre refrescos y
solo se actualiza el texto. El color del icono se fija explícitamente según
el estado, sin alternarlo al actualizar el contador.

Al configurar un nodo por LoRa, importar y enviar requieren que el servicio y la radio del gateway estén disponibles. El formulario actualiza esta condición con el estado de la red y muestra el motivo del bloqueo. La API vuelve a comprobarla antes del sondeo del nodo y antes de encolar la configuración. Los errores incluyen un código que distingue la radio desconectada, el servicio detenido, un estado desconocido, un nodo sin respuesta y una transferencia en curso; un código HTTP 409 por sí solo no significa que exista otra operación. La vista no muestra el historial de cambios e importaciones anteriores.

**Configurar radio LoRa** (`radioapi.py`, `/api/radio`): estado del servicio y del puerto, cambio del puerto del Heltec (`set_lora_port.sh`: `gateway.env`, `web.env` y reinicio del servicio) y flasheo de `heltec-radio.bin` (`flash_heltec.sh`). Las acciones privilegiadas corren con `sudo -n` bajo la regla acotada que el instalador deja en `/etc/sudoers.d/modulinkr-web`, limitada a los scripts declarados por cada página.

**Configurar zona horaria** (`settingsapi.py`, `/api/ajustes`): zona de visualización del reloj de la cabecera y de las horas de las gráficas. A diferencia de la config del servicio (variables de entorno de solo lectura), estas preferencias se escriben en un JSON propio del visor (`MODULINKR_WEB_SETTINGS`, por defecto junto al `buffer.db`), con escritura atómica y validación de la zona con `zoneinfo`. El valor es una zona IANA fija, compartida por todos los navegadores, o `auto` para que cada navegador use la suya. El frontend rellena el selector con el catálogo de zonas del navegador (`Intl.supportedValuesOf`) y ofrece un botón para detectar la del navegador; el ajuste se aplica a todas las llamadas `toLocale` del reloj y de los ejes/tooltips.

**Configurar asistente de IA** (`aiapi.py`, `modbus_ai_provider.py`, `/api/ia`): proveedor, modelo, URL base y estado de la credencial utilizada por el asistente Modbus. Se admite OpenAI con su URL fija y una API pública compatible con OpenAI mediante HTTPS. La API compatible debe implementar Responses, salida JSON estructurada y, cuando se utilicen, `input_file` y `web_search`. `GET /api/ia/estado` devuelve la configuración no secreta y un booleano de credencial; nunca devuelve su valor. `POST /api/ia/guardar` aplica una lista cerrada de campos mediante `set_ai.sh`, que recibe los valores por stdin y actualiza `/etc/modulinkr/web.env` sin reiniciar el visor. La credencial se codifica en base64 para evitar sintaxis activa al cargar el archivo de entorno; esta codificación no sustituye la protección del archivo, que conserva permisos de solo root. Dejar la clave vacía conserva la vigente.

`POST /api/ia/modbus/proponer` acepta un PDF de hasta 10 MB o la identidad del equipo. El flujo declara de forma explícita tres operaciones sin estado. `discover` revisa el alcance del documento, diferencia un modelo, una familia o varios dispositivos físicos y devuelve los targets y grupos documentales con evidencia, sin estimar cantidades de parámetros. La GUI exige seleccionar un target exacto y permite elegir hasta ocho grupos. `extract` vuelve a enviar la fuente y extrae solo ese target y esos grupos, con cobertura por sección. `refine` se utiliza únicamente cuando la selección final conserva datos pendientes. Las fases necesarias se realizan una sola vez en el proveedor externo, sin reintentos automáticos. Los IDs producidos por el modelo son temporales. El gateway asigna IDs internos únicos, reconstruye la cobertura cuando fuente y página identifican una única sección, descarta los parámetros ajenos a los grupos elegidos y mantiene el bloqueo ante cualquier ambigüedad. También valida todos los contratos, la relación entre target y secciones y rechaza resultados vacíos o incompletos. No se conserva un catálogo entre eventos de configuración. La búsqueda por fabricante y modelo utiliza `web_search`. `POST /api/ia/modbus/validar` vuelve a comprobar localmente una selección antes de que el adaptador de la GUI la copie. La propuesta rellena los campos Modbus del formulario y la función existente `buildConfig` sigue siendo la única que genera el `config.json`.

La página se bloquea si el visor no tiene usuario y contraseña o no dispone del par de certificado y clave TLS. `MODULINKR_AI_ALLOW_INSECURE_DEV=1` habilita una excepción explícita para desarrollo local y permite una API compatible por HTTP solo en loopback. Cada conexión resuelve y comprueba el destino, rechaza direcciones locales o privadas y fija el socket a una de las IP aprobadas para evitar redirecciones o cambios de resolución. Solo se admite una consulta simultánea. El cuerpo se envía con `store=false`; el tratamiento y la retención que todavía aplique el proveedor dependen de la cuenta configurada. Guardar indica que existen parámetros, pero la primera consulta real sigue siendo la comprobación efectiva de modelo, credencial y capacidades.

**Configurar base de datos** (`dbapi.py`, `/api/db`): parámetros de conexión al PostgreSQL de la VM que el módulo de datos (`dataapi.py`) usa para el histórico. Es la conexión de SOLO LECTURA (rol `modulinkr_ro`): alimenta la vista de Datos, el export CSV y el valor congelado de las tarjetas de red; la escritura la hace el consumer del servidor, con otro rol y su propio instalador. Los parámetros los consume el propio proceso del visor, así que se aplican en caliente: `set_db.sh` reescribe `web.env` (durabilidad) y, sin reiniciar el visor, se actualizan los globals de `dataapi` para que la próxima consulta use la conexión nueva. Botón de prueba con una conexión real (`psycopg2`, `SELECT 1`).

**Configurar MQTT** (`mqttapi.py`, `/api/mqtt`): parámetros del broker cloud al que el gateway publica la telemetría. A diferencia de la base de datos, estos los consume el servicio del gateway (un proceso aparte que los lee de `gateway.env` al arrancar), así que aplicar pasa por `set_mqtt.sh` (reescribe `gateway.env` y reinicia el servicio del gateway). El formulario muestra los parámetros desde una sombra no secreta guardada en los ajustes del visor, y el estado vivo de la conexión sale del latido del servicio (`gateway_status`). Botón de prueba con una conexión MQTT real (`paho`, en el venv del visor).

En las dos páginas, «Probar conexión» con la contraseña vacía utiliza la credencial vigente. La base de datos usa la que el visor tiene en memoria. MQTT la recupera en cada prueba desde la configuración protegida del gateway mediante `get_net.sh`, bajo la regla sudo existente. Se captura la salida exclusivamente en el servidor, sin registrarla, devolverla al navegador ni persistirla en los ajustes. Si la lectura protegida falla o no contiene el campo esperado, se detiene la prueba antes de conectar. Una contraseña nueva se usa solo para ese intento; probar no guarda parámetros ni reinicia servicios. «Guardar conexión» sigue conservando la contraseña anterior cuando el campo está vacío.

**Parámetros de red LoRa** (`netapi.py`, `/api/net`; camino B): edición de los parámetros que comparte todo el despliegue (Network ID, región, frecuencia, SF, ancho de banda, TTL y seguridad AES-CCM). Los valores actuales los sirve `GET /api/config/red` (`configapi.py`, vía `get_net.sh`); guardar pasa por `POST /api/net/guardar`, que aplica con `set_net.sh` (reescribe `gateway.env` y reinicia el servicio del gateway, que reempuja los parámetros de radio al Heltec con el comando `RADIO`, `frame-format.md` §12.6). Así un cambio de Network ID, frecuencia, SF o BW se aplica en caliente sin reflashear el Heltec. Validación en vivo que marca los campos fuera de rango y bloquea Guardar; la región precarga la frecuencia por defecto. Al leer un nodo, el asistente compara sus parámetros de red con los actuales y, si difieren, pregunta si actualizarlos o conservar los del nodo (desbloqueándolos para editarlos). Todo cambio obliga a reconfigurar los nodos a los mismos valores, o dejan de comunicarse, y por eso guardar pide confirmación explícita: el diálogo lista qué parámetros cambian y de qué valor a cuál, y nombra uno a uno los nodos que van a quedar incomunicados, marcando los que ya estaban sin señal. "Se perderán los nodos" y "vas a perder NodoV1 y SuperNodoV2.1" no se leen igual.

La confirmación solo aparece si cambia algo que rompa la red. Guardar sin tocar nada, o tocando solo el TTL (que altera el alcance del relay pero no impide a un nodo hablar con el gateway), guarda directamente: un aviso que salta siempre se aprende a ignorar y deja de proteger. La clave se compara contra la vigente y no contra vacío, porque el formulario la precarga y tener algo escrito no significa cambiarla.

**Configurar red WiFi** (`wifiapi.py`, `/api/wifi`): red WiFi a la que se conecta el gateway, gestionada con NetworkManager (`nmcli`). La página muestra la red actual y su IP, escanea las redes visibles (SSID, señal, seguridad) y conecta a la elegida con contraseña opcional. El escaneo y la conexión son privilegiados y van por `set_wifi.sh` (modos `scan` y `connect`) bajo la regla sudoers; el estado actual (SSID e IP) lo lee el visor sin sudo. La contraseña viaja al script por stdin y de ahí a `nmcli --ask`, nunca por argumentos; NetworkManager guarda el perfil (persiste a reinicios) y el visor no escribe la contraseña en ningún archivo propio. Conectar a una red distinta cambia la IP del gateway y puede cortar la sesión si el navegador entra por ese mismo WiFi; el acceso por `gateway.local` (mDNS) no depende de la IP.

**Herramientas de depuración** (`debugapi.py`, `/api/debug`): tres visores de log en vivo por Server-Sent Events (SSE), que el navegador consume con EventSource y el frontend cierra al salir de la página. El journal del servicio del gateway (`journalctl -u modulinkr-gateway`, leído sin sudo porque el instalador añade el usuario del servicio al grupo `systemd-journal`), la salida serie de un nodo conectado por USB (bajo el lock serie de `configapi`, con el puerto del Heltec excluido; mientras el monitor está abierto no se puede comisionar, comparten el bus) y las tramas modbus-debug filtradas por nodo (líneas `modbus-debug origin=<id>` del journal; solo aparecen si el nodo tiene `modbus.debug=true`, `node-config.md` §5). Cada stream mata su `journalctl` o cierra el puerto al desconectar el cliente.

El diagnóstico de nodos 0.0.61 y radio 0.3.3 usa registros alineados con tiempo, nivel, componente y evento. Las tres pestañas del visor incorporan filtros por nivel, componente y texto, conservan las líneas completas con desplazamiento horizontal y evitan la traducción de los datos técnicos. El formato, las excepciones de protocolo y los límites de validación se describen en [`diagnostic-logs.md`](../../shared/diagnostic-logs.md).

## 6.1 Configuración de nodo (2026-07-24)

El menú "Configurar nodo" tiene tres páginas. **Cargar firmware** flashea el firmware del Atom por USB (endpoints `/api/config/firmware` y `/api/config/flash` de `configapi.py`, `flash_nodo.sh` bajo la regla sudoers); elige el puerto candidato sin sondear el protocolo, porque un Atom virgen no responde. **Cargar nodo vía USB** es la carga directa de un `config.json` (`CFG.PUT`) descrita arriba.

**Nodo conectado al equipo local (Web Serial).** Las páginas de firmware, "Cargar nodo vía USB" y el monitor serie de depuración llevan un selector de fuente: el nodo puede estar en el USB del gateway (lo maneja la Pi, lo descrito arriba) o en el USB del ordenador donde corre el navegador. En modo local, el navegador habla el puerto por Web Serial (Chrome o Edge de escritorio, sobre HTTPS): el monitor serie lee el puerto; la carga de config reimplementa el mismo protocolo `CFG.*` (`CFG.HELLO`/`GET`/`PUT`/`DEL`) desde el navegador; y el flasheo usa **esptool-js** (bundle servido del vendor, `get_vendor.sh`, así que funciona sin Internet) para escribir el binario (`/api/config/nodo-bin`, servido por `configapi.py`) en `0x0` con `eraseAll:false`, de modo que **conserva el `config.json`** del nodo igual que el flasheo por la Pi (a diferencia de esp-web-tools, que borra la flash entera). El nodo se resetea solo al terminar. Sirve para aprovisionar nodos desde el portátil sin pasar por la Pi.

**Asistente de configuración de nodo** arma el `config.json` desde un formulario, sin escribir JSON a mano. Secciones de identidad, LoRa, mesh, NB-IoT (solo si el tipo es supernodo) y Modbus con las cuatro clases de lectura y las escrituras, cada una con filas `+`/`-`; el avanzado por sección queda plegado. La generación del JSON es una función pura que aplica las reglas del schema (omite opcionales por defecto, `byte_order` solo en tipos de 32 bits, `type` fuera de coils y discrete inputs, `nbiot` solo en supernodo). Un principio de visibilidad rige todo el formulario: un campo que no aplica se oculta (`byte_order` según el tipo, los campos de registro según la función de escritura, el cambio de slave_id solo si difieren de fábrica).

Los parámetros que, cambiados, impedirían al nodo unirse a la red o publicar en la misma nube se bloquean a los valores reales del gateway, leídos con `get_net.sh` (`/api/config/red`): región y frecuencia (que el gateway no guarda, fijados al despliegue), Network ID, TTL, SF, ancho de banda, la seguridad (activada y clave) y el broker MQTT (host, puerto, TLS, usuario y clave).

El asistente arranca preguntando la intención, y no por el formulario. **Configurar nodo nuevo** propone el primer ID libre, deja en blanco lo propio del nodo y quita la opción de LoRa del selector de destino, porque un nodo sin configurar no está en la red. **Reconfigurar nodo existente** pide el nodo una sola vez: leerlo rellena el formulario y ese mismo nodo queda como destino del envío. Antes se preguntaba dos veces, arriba para leer y abajo con "Buscar nodo" antes de enviar, que por radio ni siquiera buscaba nada.

El `config.json` se regenera con cada cambio del formulario en una caja de solo lectura, sin botón de validar: la validación ya corría en cada tecla para marcar los campos, así que el botón solo añadía un estado (validado o no) que toda edición invalidaba. La caja no se edita a mano a propósito, para no tener dos fuentes de verdad sobre el mismo dato; para JSON en crudo está la página de carga por USB. Tres botones la acompañan: cargar de archivo (que rellena el formulario, y si el archivo no parsea no lo toca), guardar en archivo y copiar. La carga de archivo es el único punto donde los errores se muestran agrupados, porque un archivo puede venir mal por diez sitios a la vez y diez campos en rojo repartidos por la página no se ven.

Enviar exige dos cosas a la vez: destino confirmado y formulario sin errores. El destino es contexto de la sesión y no lo tumba una edición; solo lo tumba cambiar de fuente. Por cable, leer el nodo ya lo fija y compara la versión que anuncia (`CFG.HELLO`) con la de `nodo.bin` (`nodo.bin.version`): con la última versión "Enviar al nodo" carga solo la configuración; desactualizado o virgen, carga el firmware y después la configuración. Por radio el destino es el nodo elegido en la lista, que muestra la versión de firmware que cada uno declaró en su `NODE_REGISTER`; es un dato de cuando arrancó, de modo que un nodo actualizado por cable y sin volver a registrarse aparecerá atrasado.

**Actualización de firmware por LoRa** (`otaapi.py`, `/api/config/lora/firmware*`; `frame-format.md` §18). En la página de firmware, la fuente "nada: enviar por LoRa" sube la imagen al nodo por radio. Lo que viaja es `nodo-app.bin`, la aplicación sola que genera `nodo/make_dist.sh`, no el `nodo.bin` completo: ese lleva además gestor de arranque y tabla de particiones, que en una actualización en caliente no se tocan, y es el doble de grande.

La subida tarda horas (unos 2500 fragmentos, 16 minutos de tiempo de aire repartidos respetando el ciclo de trabajo) y la ejecuta el servicio del gateway, no el navegador: el visor solo encola en la tabla `fw_push`, sondea el progreso cada diez segundos y, cuando el nodo tiene la imagen entera y verificada, ofrece instalarla. La ventana horaria (por defecto de 23:00 a 06:00) acota cuándo puede transmitir.

Instalar es una orden aparte y con confirmación, porque subir es inocuo y puede correr de noche sin vigilancia mientras que instalar reinicia el nodo. El visor se niega a enviar si el binario en disco no coincide con su `.sha256`, que es medio segundo de comprobación frente a horas de radio desperdiciadas.

La tarjeta del gateway en la vista Red muestra dos enlaces independientes desde el latido de estado del servicio (`gateway_status`): un chip LoRa (radio del Heltec) y un chip MQTT (conexión al broker cloud). El servicio refresca el latido cada `MODULINKR_HEARTBEAT_S`, y ante una desconexión del Heltec marca `lora_link=0` en el acto: el chip LoRa pasa a "sin señal" en el siguiente sondeo de la web (unos segundos), sin esperar el hueco del auto-reporte de aire (antes hasta `MODULINKR_WEB_ONLINE_S`). El chip MQTT distingue conectado, sin conexión y no configurado, y es ortogonal al de LoRa (la nube puede estar arriba con la radio caída y viceversa). `netstatus.gateway_link_state` da el servicio por caído si el latido no se refresca dentro de `MODULINKR_WEB_HEARTBEAT_S` (default 15 s); un buffer anterior a la tabla `gateway_status` cae al veredicto antiguo del auto-reporte de aire con un solo chip.

La tarjeta de cada nodo muestra indicadores separados para LoRa, Modbus y, en los supernodos, NB-IoT y MQTT. Modbus se obtiene de `st_code` de la última telemetría (`frame-format.md` §4). NB-IoT y MQTT combinan el diagnóstico del heartbeat (`frame-format.md` §6.1) con publicaciones celulares observadas, según las reglas de §4. El supernodo consulta el registro celular y la sesión MQTT cada 60 s por UART. Un fallo exclusivo de MQTT conserva NB-IoT conectado cuando el registro celular sigue confirmado. Un diagnóstico caducado o una consulta sin respuesta se presentan como estado desconocido. La ruta LoRa completa y vigente tiene prioridad; la entrega mediante supernodo aparece en amarillo cuando se confirma por la vía celular.

## 7. Descartes razonados

- **Grafana**: cubre solo la función 1, pesa 150-300 MB (inviable en el Zero 2W) y obligaría a construir igualmente el resto. Queda como opción futura en la VM apuntando al mismo Postgres si algún día hace falta análisis avanzado.
- **Home Assistant**: el patrón modular se copia; la plataforma no (dimensionada para cientos de integraciones domóticas, ajena a este dominio).
- **Web en la VM**: era la opción recomendada por cercanía al dato histórico, descartada en favor del Pi para tener el estado vivo de la red en campo sin depender de Internet.

## 8. Cifrado TLS (2026-07-25)

El visor sirve HTTPS. `uvicorn` termina el TLS con un certificado autofirmado que genera el instalador (`web.sh`, `web_make_cert`) la primera vez y conserva en reinstalaciones (regenerarlo invalidaría el que el operador ya haya marcado como de confianza). El par vive bajo el árbol del visor (`pi-web/.tls/`, gitignored), propiedad del usuario del servicio para que `uvicorn` lo lea sin permisos de root; las rutas van a `web.env` (`MODULINKR_WEB_CERT`, `MODULINKR_WEB_KEY`) y la unidad las pasa con `--ssl-certfile`/`--ssl-keyfile`. El puerto por defecto pasa a 8443.

El certificado incluye SAN por nombre mDNS (`<host>.local`, la vía de acceso normal) y por la IP del momento; el acceso por `<host>.local` no depende de la IP, que puede cambiar por DHCP. Al ser autofirmado, el navegador avisa la primera vez; el visor sirve la parte pública en `GET /cert` (público, es la parte pública del par) con un enlace en la página de login, para instalarlo como de confianza en el dispositivo y quitar el aviso. La cookie de sesión se marca `Secure` cuando hay TLS configurado; el arranque manual de banco sin certificado la deja sin el flag para no romper el login sobre HTTP.

Descarte: no se monta una CA propia ni `mkcert`. Para un gateway de LAN al que se accede desde pocos dispositivos, el certificado autofirmado descargable da el mismo canal cifrado con menos partes móviles; una CA solo compensaría con muchos dispositivos o muchos gateways.


## Identificación y actualización del firmware

Las pantallas muestran la versión instalada y la versión disponible en el gateway, sin identificadores de compilación. Solo se ofrece actualizar cuando la versión disponible es superior. Una versión igual se presenta como «Actualizado» y una versión anterior no permite retroceder. Los firmwares antiguos que anuncian, por ejemplo, `0.0.58-difusion-red` se reconocen como `0.0.58`. Si no se puede leer una versión válida, se solicita repetir la identificación y no se habilita una actualización normal.

La versión de prueba del nodo es `0.0.60` y la de la radio es `0.3.2`, sin cambios funcionales en el dispositivo respecto a `0.0.59` y `0.3.1`, respectivamente. Estas versiones permiten comprobar de nuevo los recorridos de actualización. Los cambios funcionales distribuidos requieren una versión nueva. El empaquetado verifica que la versión del binario coincida con la declarada en el código y rechaza sustituir una distribución por otra aplicación distinta con el mismo número. Se puede repetir el empaquetado del mismo firmware. «Ver cambios» presenta las notas correspondientes al componente y a la versión disponible.

La identidad interna conserva el formato `version+compilacion`, con los primeros doce caracteres hexadecimales del SHA-256 del ELF, dentro del límite de 32 bytes existente. `shared/firmware_identity.h` la obtiene del descriptor ESP32. `firmwaremeta.py` lee el mismo descriptor y el marcador `MLFW:` del binario. Esa identidad se utiliza para validar la imagen distribuida y confirmar la instalación, sin trasladar sus detalles a la interfaz.

El firmware de nodos presenta dos opciones: «Por LoRa» y «Por USB». En USB se elige entre este equipo y el gateway y se identifica el nodo antes de ofrecer una actualización. Antes de escribir se comprueba de nuevo la versión. En USB de este equipo, los controles permanecen bloqueados hasta cerrar la sesión. La identificación nueva sustituye a la anterior únicamente después de cerrar el puerto; una sesión que no pudo abrirlo no cierra el de otra operación. Tras escribir y reiniciar, se consulta el mismo nodo para confirmar su versión. Se muestra un diálogo de resultado y se conserva el mensaje en la página. Si no se puede confirmar el arranque, se indica que la escritura terminó y que es necesario repetir la identificación. En USB del gateway se mantiene la comprobación manual posterior al reinicio.

Por LoRa se seleccionan uno o varios nodos en una única tabla. Se guardan envíos dirigidos en `fw_bcast`, con los campos existentes y en el orden seleccionado. El gateway los atiende por turnos y la cola continúa aunque se cierre el navegador. La selección parcial no emite una oferta de difusión a nodos no seleccionados. El coste de radio aumenta con cada destinatario porque se utiliza un envío dirigido por nodo. Se conserva la ruta individual existente para nodos con saltos intermedios. Las difusiones anteriores siguen visibles mientras requieren atención.

La tabla distingue cola, envío, imagen lista, instalación y confirmación. La instalación se habilita al terminar los envíos y requiere confirmación por nodo. Se mantiene la comprobación mediante un mapa nuevo `FW_BCAST_POLL`, con un máximo de 30 segundos para responder. El veredicto de instalación debe identificar la imagen solicitada; si se pierde, se requiere un catálogo posterior a la orden que anuncie la identidad esperada, dentro de diez minutos. Un mapa completo no se interpreta como instalación confirmada. Cancelar detiene los envíos pendientes y conserva las imágenes ya recibidas.

La radio responde a `FW?` por USB sin reiniciarse para esa consulta. El servicio también reconoce la versión del banner de los firmwares anteriores y no vacía ese banner al abrir el puerto. La identidad se elimina al iniciar una conexión serie nueva y permanece vigente durante esa conexión. La API requiere una identidad del puerto configurado, un enlace disponible y un registro refrescado en los últimos 60 segundos. La recepción del banner antiguo depende de que la radio lo emita durante la conexión; no se inventa una versión cuando no responde. Para esa situación se dispone de «Recuperación de la radio», una reinstalación USB explícita, separada del flujo normal y con confirmación de la interrupción de LoRa.

Los avisos de estas pantallas reciben su tipo explícitamente. Un fallo de identificación no se convierte en éxito por contener la palabra «instalada» y solo se anima un indicador durante una operación real.

La validación local incluye comparación de versiones, rechazo de empaquetado incoherente, cola dirigida con SQLite temporal, compatibilidad del banner de radio, comprobación de imagen y veredicto, y controles de interfaz. La revisión visual utiliza la aplicación real con respuestas simuladas. La compilación ESP32, el acceso USB y los envíos LoRa requieren validación en el banco.

### Despliegue de estas pantallas y su identificación de firmware

En el Mac, se compila primero cada proyecto desde VS Code con `PlatformIO: Build`: `firmware/nodo` y `firmware/gateway/heltec-radio`. Después se empaquetan ambos binarios:

```bash
cd "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/nodo" && ./make_dist.sh
cd "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/gateway/heltec-radio" && ./make_dist.sh
```

En el Mac, se prepara y copia el paquete de despliegue:

```bash
tar -czf /tmp/modulinkr-firmware-ui.tar.gz \
  -C "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/gateway" \
  pi-web/static/app.js pi-web/static/index.html pi-web/static/style.css \
  pi-web/configapi.py pi-web/radioapi.py pi-web/otaapi.py pi-web/firmwaremeta.py \
  pi-service/gateway_service.py pi-service/buffer.py \
  pi-service/nodo.bin pi-service/nodo.bin.version \
  pi-service/nodo-app.bin pi-service/nodo-app.bin.version pi-service/nodo-app.bin.sha256 \
  pi-service/heltec-radio.bin
scp /tmp/modulinkr-firmware-ui.tar.gz modulinkr@Gateway.local:~/modulinkr-firmware-ui.tar.gz
```

En la sesión Termius del gateway, cuando no haya un envío o instalación en curso, se aplica el paquete a los directorios declarados por los servicios. Este paso reinicia el servicio de radio y el visor:

```bash
bash <<'SH'
set -eu
fw_web_dir="$(systemctl show modulinkr-web.service --property=WorkingDirectory --value)"
fw_service_dir="$(systemctl show modulinkr-gateway.service --property=WorkingDirectory --value)"
test -n "$fw_web_dir" && test -d "$fw_web_dir/static"
test -n "$fw_service_dir" && test -f "$fw_service_dir/gateway_service.py"
fw_stage="$(mktemp -d /tmp/modulinkr-fw-ui.XXXXXX)"
trap 'rm -rf "$fw_stage"' EXIT
tar -xzf "$HOME/modulinkr-firmware-ui.tar.gz" -C "$fw_stage"
python3 "$fw_stage/pi-web/firmwaremeta.py" "$fw_stage/pi-service/nodo.bin"
python3 "$fw_stage/pi-web/firmwaremeta.py" "$fw_stage/pi-service/nodo-app.bin"
python3 "$fw_stage/pi-web/firmwaremeta.py" "$fw_stage/pi-service/heltec-radio.bin"
sudo systemctl stop modulinkr-web.service modulinkr-gateway.service
for fw_file in static/app.js static/index.html static/style.css configapi.py radioapi.py otaapi.py firmwaremeta.py; do
  sudo install -m 644 "$fw_stage/pi-web/$fw_file" "$fw_web_dir/$fw_file"
  cmp "$fw_stage/pi-web/$fw_file" "$fw_web_dir/$fw_file"
done
for fw_file in gateway_service.py buffer.py nodo.bin nodo.bin.version nodo-app.bin nodo-app.bin.version nodo-app.bin.sha256 heltec-radio.bin; do
  sudo install -m 644 "$fw_stage/pi-service/$fw_file" "$fw_service_dir/$fw_file"
  cmp "$fw_stage/pi-service/$fw_file" "$fw_service_dir/$fw_file"
done
sudo systemctl start modulinkr-gateway.service modulinkr-web.service
systemctl is-active modulinkr-gateway.service modulinkr-web.service
SH
```

Se recarga el navegador con `Cmd+Shift+R`. La copia de los binarios al gateway no los instala en los dispositivos. La instalación se realiza desde las pantallas correspondientes. Si la radio antigua no anuncia su versión, se utiliza «Recuperación de la radio» para instalar `0.3.2`. Se comprueba después que informe esa versión y que desaparezca la actualización normal. En los nodos se comprueba la identificación USB, la transición de `0.0.59` a `0.0.60`, la ausencia de reinstalación de la misma versión y el envío a los destinatarios seleccionados por LoRa.
