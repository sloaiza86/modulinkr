# Barrido previo a la primera publicación

Revisión del 2026-10-14 sobre nodo, gateway, visor, consumidor, contratos, instaladores, herramientas y configuración. ModuLinkr sigue en desarrollo y todos los despliegues reales partirán con identidad física. Los formatos anteriores del banco no representan clientes publicados.

## Hallazgos y correcciones

| Tratamiento encontrado | Corrección aplicada |
| --- | --- |
| Incidencia `legacy_pending` del nodo 0 generada al conservar un buffer interno | No se crea una incidencia por archivar datos de desarrollo; se retiran las ya guardadas |
| Avisos `unverified` por formatos de radio anteriores | La recepción admite exclusivamente `0x40`; el formato rechazado no se presenta como un equipo sin identidad |
| «Histórico anterior a la identidad física» y consulta alternativa a `public` | Se elimina selector, parámetro y conexión alternativa; las consultas exigen el despliegue actual |
| Filas sin UID copiadas o retenidas en el buffer operativo | Se conserva el original en tablas internas; se apartan del backlog operativo |
| Catálogos de prueba sin UID utilizados como catálogo vigente | Se conservan fuera del catálogo operativo cuando el gateway ya tiene identidad registrada |
| Outbox que importaba MOB1 o MOB2 como muestras sin equipo | Se archiva el archivo original y se utiliza MOB3; las entradas sin UID o sesión no vuelven a la cola |
| Suscripción e ingesta MQTT v1, conversión de salud 4.0 a 3.0 | Se retiran esos caminos y los dos módulos de ingesta anteriores; solo v2 y sobre 4.0 |
| Temas MQTT editables aunque se derivan del despliegue | Se retiran controles, campos generados, miembro de configuración y temas v1 |
| Configuración 3.0, 3.1 y 3.2 admitida por el nodo | El único contrato admitido es 3.3 |
| `modbus.debug` booleano transformado automáticamente | Se admiten exclusivamente los cinco modos actuales; ausencia equivale a `off` |
| `poll_interval_ms` ignorado como opción anterior | Se rechaza; el periodo se determina mediante `send_interval_ms` |
| Destino sin declaración de formato con botón Continuar | Se bloquea y se indica identificar de nuevo el nodo o consultar su catálogo |
| Configuración sin sondeo para versiones internas anteriores | La disponibilidad se consulta siempre antes de encolar |
| HEARTBEAT vacío, NODE_HEALTH de 25 bytes y CONFIG_COMMIT de 38 | Se exige el contrato actual: 4 o 6, 29 y 42 bytes, respectivamente |
| Catálogo mínimo sin contrato ni descriptores actuales | Se rechaza antes de registrarlo |
| Confirmaciones OTA para gateways anteriores y transferencia cero como sustituto | El nodo deja de repetir FW_RESULT al confirmar arranque; FW_REPORT con transferencia y SHA-256 confirma la instalación |
| FW_RESULT utilizado como veredicto de arranque | Solo se procesa rechazo inmediato vinculado a la transferencia exacta |
| CLI Heltec con un decodificador anterior duplicado | Se utiliza el analizador común de radio 4.0 |
| Versiones obtenidas desde un archivo auxiliar de imágenes antiguas | Se usa la identidad real de la imagen; el paquete completo exige coincidencia con el código actual |
| Documentos que describían v1, claves sin UID y formatos previos como vigentes | Se actualizan los contratos MQTT, comandos, base, configuración y arquitectura; la evolución de radio se identifica como antecedente interno |

Se rechazan también temas MQTT con despliegue nulo, publicador fuera de rango o salud y registro atribuidos al gateway. Una discrepancia entre ID del tema de salud y payload no se guarda. Estos rechazos se confirman al broker para no provocar una reentrega infinita de entradas inválidas.

## Tratamientos conservados

Se conserva el histórico normal de medidas y catálogos, la opción «Mostrar dispositivos y medidas anteriores», la reversión de configuración y firmware, la recuperación USB y las actualizaciones futuras. Los conflictos físicos reales permanecen visibles, persistentes y protegidos frente a órdenes.

Las migraciones SQLite y PostgreSQL aplicadas se mantienen para recuperar y reproducir el banco. Los datos anteriores se conservan internamente, sin convertir esa conservación en una opción o incidencia del producto. Los registros de la bitácora son históricos y no se reescriben.

La representación privada de once bytes de los constructores Python permite serializar payloads antes de formar la cabecera de radio 4.0. No es una vía de recepción anterior: el analizador público exige `0x40`. Se conserva para evitar una reescritura de todos los constructores ajena al problema.

La compatibilidad con respuestas AT del SIM7028, banners RAK, bibliotecas instaladas, proveedores externos y variantes Modbus sigue siendo necesaria. Los mensajes serie sin formato estructurado también pueden proceder de módulos externos o del arranque. No representan usuarios de versiones antiguas de ModuLinkr.

Los valores predeterminados documentados del contrato actual siguen siendo válidos cuando el campo es opcional, por ejemplo clase C y diagnóstico apagado. No se confunde un catálogo sin declaración con esa configuración válida: el catálogo operativo debe declarar su clase y formato.

## Comprobaciones locales

Se superan 578 pruebas Python: 53 del nodo, 107 del servicio del gateway, 398 del visor y 20 del consumidor. Se ejecutan partes reales de C++ con hardware simulado, SQLite temporal y PostgreSQL temporal PGlite con las seis migraciones. Se comprueban conservación del archivo original, retirada de avisos persistidos, selección exclusiva de despliegue, contratos rechazados, conflictos reales, disponibilidad obligatoria y confirmación OTA con hash.

Se superan 88 comprobaciones JavaScript, incluidas cuatro pruebas de interfaz en Chrome local con respuestas simuladas. Se comprueba ausencia del selector y de los campos MQTT, rechazo sin botón Continuar, resolución de conflictos, histórico normal, cuarentena y operaciones del nodo.

Se comprueba la sintaxis de 41 módulos Python, dos instaladores y los dos scripts JavaScript modificados. El paquete de servicios no contiene firmware, configuraciones de nodo ni credenciales. El paquete del servidor incluye salud 4.0. El empaquetado completo se rechaza mientras la imagen distribuida 0.0.82 no coincida con el código 0.0.83. Las pruebas del instalador comprueban la validación previa sin imagen; la recuperación operativa y la disponibilidad HTTP requieren contraste en el gateway.

La compilación nativa conserva un aviso previo de `modbus.cpp` sobre una comparación de un uint8 con 2000. No se modifica esa lógica durante este barrido. No se ejecuta PlatformIO Build ni se despliegan servicios o imágenes. La validación física y los permisos reales de broker y PostgreSQL permanecen pendientes.

## Despliegue

Los comandos completos de Mac, Termius del gateway y Termius de la VM se encuentran en [`identity-safety.md`](identity-safety.md). El paquete `gateway-runtime` permite retirar los avisos y la opción de histórico inmediatamente después de instalar y recargar el visor, sin esperar un Build del nodo. Las restricciones del firmware requieren compilar, empaquetar e instalar 0.0.83.

## Archivos del barrido

El inventario se determina mediante las rutas y hashes de inicio de esta tarea, sin comandos Git. Las imágenes distribuidas permanecen en 0.0.82 y no forman parte de las modificaciones de este barrido. Los cambios previos de la implementación de identidad se enumeran por separado en [`identity-files.md`](identity-files.md).

### Dentro del repositorio

- Modificado: [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
- Modificado: [`README.md`](../../README.md)
- Modificado: [`gateway/pi-service/README.md`](../../gateway/pi-service/README.md)
- Modificado: [`gateway/pi-service/buffer.py`](../../gateway/pi-service/buffer.py)
- Modificado: [`gateway/pi-service/gateway_service.py`](../../gateway/pi-service/gateway_service.py)
- Modificado: [`gateway/pi-service/heltec_rx_parser.py`](../../gateway/pi-service/heltec_rx_parser.py)
- Modificado: [`gateway/pi-service/identity.py`](../../gateway/pi-service/identity.py)
- Modificado: [`gateway/pi-service/install_identity.sh`](../../gateway/pi-service/install_identity.sh)
- Modificado: [`gateway/pi-service/mqtt_publisher.py`](../../gateway/pi-service/mqtt_publisher.py)
- Modificado: [`gateway/pi-service/protocol.py`](../../gateway/pi-service/protocol.py)
- Modificado: [`gateway/pi-service/tests/test_firmware_install_check.py`](../../gateway/pi-service/tests/test_firmware_install_check.py)
- Modificado: [`gateway/pi-service/tests/test_modbus_commands.py`](../../gateway/pi-service/tests/test_modbus_commands.py)
- Modificado: [`gateway/pi-service/tests/test_node_maintenance.py`](../../gateway/pi-service/tests/test_node_maintenance.py)
- Modificado: [`gateway/pi-service/tests/test_physical_identity.py`](../../gateway/pi-service/tests/test_physical_identity.py)
- Modificado: [`gateway/pi-service/tests/test_route_trace.py`](../../gateway/pi-service/tests/test_route_trace.py)
- Modificado: [`gateway/pi-web/README.md`](../../gateway/pi-web/README.md)
- Modificado: [`gateway/pi-web/configapi.py`](../../gateway/pi-web/configapi.py)
- Modificado: [`gateway/pi-web/dataapi.py`](../../gateway/pi-web/dataapi.py)
- Modificado: [`gateway/pi-web/firmwaremeta.py`](../../gateway/pi-web/firmwaremeta.py)
- Modificado: [`gateway/pi-web/netstatus.py`](../../gateway/pi-web/netstatus.py)
- Modificado: [`gateway/pi-web/otaapi.py`](../../gateway/pi-web/otaapi.py)
- Modificado: [`gateway/pi-web/static/app.js`](../../gateway/pi-web/static/app.js)
- Modificado: [`gateway/pi-web/static/identity.js`](../../gateway/pi-web/static/identity.js)
- Modificado: [`gateway/pi-web/static/index.html`](../../gateway/pi-web/static/index.html)
- Modificado: [`gateway/pi-web/supervision.py`](../../gateway/pi-web/supervision.py)
- Modificado: [`gateway/pi-web/tests/test_identity_dom.cjs`](../../gateway/pi-web/tests/test_identity_dom.cjs)
- Modificado: [`gateway/pi-web/tests/test_identity_quarantine.py`](../../gateway/pi-web/tests/test_identity_quarantine.py)
- Modificado: [`gateway/pi-web/tests/test_lora_availability.py`](../../gateway/pi-web/tests/test_lora_availability.py)
- Modificado: [`gateway/pi-web/tests/test_node_config_messages_ui.cjs`](../../gateway/pi-web/tests/test_node_config_messages_ui.cjs)
- Nuevo: [`gateway/pi-web/tests/test_prepublic_package.py`](../../gateway/pi-web/tests/test_prepublic_package.py)
- Modificado: [`gateway/pi-web/tests/test_supervision.py`](../../gateway/pi-web/tests/test_supervision.py)
- Modificado: [`nodo/README.md`](../../nodo/README.md)
- Modificado: [`nodo/configs/nodo2-escrituras-wt901c485.json`](../../nodo/configs/nodo2-escrituras-wt901c485.json)
- Modificado: [`nodo/configs/nodo2.json`](../../nodo/configs/nodo2.json)
- Modificado: [`nodo/src/config.cpp`](../../nodo/src/config.cpp)
- Modificado: [`nodo/src/config.h`](../../nodo/src/config.h)
- Modificado: [`nodo/src/lora.cpp`](../../nodo/src/lora.cpp)
- Modificado: [`nodo/src/main.cpp`](../../nodo/src/main.cpp)
- Modificado: [`nodo/src/nodeclock.cpp`](../../nodo/src/nodeclock.cpp)
- Modificado: [`nodo/src/nodeclock.h`](../../nodo/src/nodeclock.h)
- Modificado: [`nodo/src/outbox.cpp`](../../nodo/src/outbox.cpp)
- Modificado: [`nodo/tests/test_route_trace.py`](../../nodo/tests/test_route_trace.py)
- Modificado: [`nodo/tests/test_write_catalog.py`](../../nodo/tests/test_write_catalog.py)
- Modificado: [`server/README.md`](../../server/README.md)
- Modificado: [`server/consumer/README.md`](../../server/consumer/README.md)
- Retirado: `server/consumer/catalog.py`
- Modificado: [`server/consumer/consumer_service.py`](../../server/consumer/consumer_service.py)
- Modificado: [`server/consumer/health.py`](../../server/consumer/health.py)
- Retirado: `server/consumer/ingest.py`
- Modificado: [`server/consumer/tests/test_supervision.py`](../../server/consumer/tests/test_supervision.py)
- Modificado: [`server/install_identity.sh`](../../server/install_identity.sh)
- Modificado: [`shared/protocol/batch-format.md`](../../shared/protocol/batch-format.md)
- Modificado: [`shared/protocol/commands-format.md`](../../shared/protocol/commands-format.md)
- Modificado: [`shared/protocol/db-schema.md`](../../shared/protocol/db-schema.md)
- Modificado: [`shared/protocol/frame-format.md`](../../shared/protocol/frame-format.md)
- Modificado: [`shared/protocol/identity-files.md`](../../shared/protocol/identity-files.md)
- Modificado: [`shared/protocol/identity-safety.md`](../../shared/protocol/identity-safety.md)
- Modificado: [`shared/protocol/node-config.md`](../../shared/protocol/node-config.md)
- Modificado: [`shared/protocol/package_identity.py`](../../shared/protocol/package_identity.py)
- Nuevo: [`shared/protocol/prepublic-audit.md`](../../shared/protocol/prepublic-audit.md)

### Fuera del alcance funcional del repositorio

Se añade la evolución técnica a `TFM/bitacora.md`, sin reescribir entradas anteriores. Las instrucciones locales `TFM/AGENTS.md` y `firmware/AGENTS.md` conservan el estado previo a la publicación; no forman parte de los commits del proyecto. El mismo criterio se guarda en la memoria local autorizada. Estos archivos locales no requieren commit ni tag.
