# Configuración Mesh, USB y métricas del gateway

Estado de implementación: 2026-10-15. El nodo declara 0.0.86. La compilación ESP32 con PlatformIO, el despliegue y la comprobación física siguen pendientes.

## Configuración del nodo

El formulario Configurar nodo reúne el alta inicial, la edición y las herramientas de archivo. El alta utiliza USB desde el navegador. La edición permite USB o Red Mesh. Se retira la página independiente Archivo de configuración y las operaciones serie del backend del gateway, incluidas la programación del Atom, la preparación del RAK y el monitor USB. La conexión operativa entre el gateway y su radio sigue siendo serie; no es una alternativa de comisionamiento.

Red Mesh consulta las rutas vigentes. Se prioriza LoRa cuando la radio del gateway y un recorrido válido están disponibles. Si el sondeo LoRa no obtiene respuesta se intenta la salida celular antes de encolar. En otro caso se utiliza la ruta NB-IoT observada, con el supernodo como publicador celular y sus relés. La operación conserva el recorrido seleccionado; una pérdida de esa ruta agota su plazo sin declarar éxito. Una nueva petición vuelve a resolver la ruta disponible.

La lectura y escritura celulares utilizan los estados de `config_read` y `config_push`. Se añaden `transport`, `route_json` y `publisher`. Las peticiones celulares no son recogidas por el emisor LoRa. La envoltura celular y las tramas 0x2E y 0x2F se describen en [`frame-format.md`](frame-format.md), §28. Los fragmentos celulares llevan hasta 144 bytes de configuración para dejar espacio al recorrido dentro del payload cifrado. Se admiten hasta 32 fragmentos. La ruta LoRa mantiene fragmentos de escritura de 176 bytes. La lectura utiliza fragmentos de 144 bytes en la ruta celular y 176 en LoRa. El contenido para aplicar una configuración remota no puede superar los 4096 bytes del reensamblado del nodo. Una lectura que exceda los 32 fragmentos de su ruta se rechaza con resultado explícito.

El gateway reintenta cada veinte segundos lo que no se ha confirmado y cierra la operación a los seiscientos segundos. El mapa de fragmentos permite reparar la entrega; la escritura termina únicamente con `CONFIG_RESULT`. El nodo valida SHA-256, contenido e identidad antes de escribir. Se reutilizan el respaldo y la reversión. La confirmación de aplicación indica escritura aceptada, no verificación del arranque posterior. En el supernodo se espera el envío MQTT del resultado antes de reiniciar, con un máximo de treinta segundos. El recorrido de relés utiliza el retorno explícito.

La GUI muestra registro celular, sesión MQTT, señal CSQ y dBm y antigüedad del diagnóstico del supernodo. Un dato vencido se identifica como última observación. No se deduce registro celular de una mera entrega LoRa.

## Firmware y presupuesto de radio

La actualización remota se presenta como Red Mesh y se limita a nodos con enlace LoRa directo al gateway. La API rechaza destinos con saltos intermedios. Se mantiene la separación entre versión en ejecución, disponible y correspondiente a cada operación, así como entre recepción, verificación, instalación y confirmación del arranque.

`allow_duty_override` se guarda en cada fila de `fw_bcast`, con valor inicial cero. El control Permitir sobrepasar el límite de duty cycle permite que esa operación supere el 10 %. Al permanecer desactivado se aplica el presupuesto horario y su reserva. El permiso no modifica permanentemente el gateway ni las otras operaciones. Su estado se muestra en la confirmación del envío y en el detalle de la operación.

USB utiliza Web Serial para identificar y programar el nodo o la radio conectados al navegador. La imagen del nodo conserva NVS y configuración. La radio comprueba ESP32-S3 y confirma `FW?` tras escribir. Los scripts de flasheo del gateway se retiran también del instalador y de su regla sudoers.

## Histórico del gateway

`gateway_airtime` conserva los tiempos de aire calculados para las transmisiones ordenadas durante la última hora. El presupuesto se reconstruye desde esos eventos tras reiniciar el servicio. `gateway_metrics` guarda `ts`, `duty_pct`, `online_lora`, `online_nbiot`, `online_total` y la situación de cada nodo. La primera observación y cada cambio de duty cycle o de conectividad generan un evento. La comprobación periódica detecta también la caducidad de rutas y de tiempo de aire. Un cambio de nodos que conserve los mismos recuentos también queda registrado.

Las dos vías se cuentan por separado. Un nodo con LoRa y NB-IoT disponibles aparece en ambos recuentos y una sola vez en `online_total`. Son rutas con evidencia vigente, no un inventario de módems instalados. El tiempo de aire del gateway procede de las órdenes de transmisión y sus parámetros de radio; no sustituye una medición física de RF.

Los eventos pendientes se publican con QoS 1 en `modulinkr/v2/<deployment_id>/255/metrics`. SQLite los conserva hasta PUBACK. El consumidor confirma al broker después de guardar en PostgreSQL y deduplica por instante e identificador de evento. La migración 007 crea la tabla en los despliegues existentes y en los posteriores, con acceso del lector del visor y separación entre despliegues.

La API expone cuatro canales de gateway con identificadores internos -1 a -4. El resumen de red permite abrir su histórico con el mismo componente de las medidas. Visualización de datos permite seleccionarlos y combinarlos por unidad. Los gráficos mantienen el valor anterior hasta el cambio siguiente, incluido el inicio del intervalo consultado y su extremo final. El CSV exporta únicamente cambios almacenados, sin convertir los extremos del gráfico en muestras de la base.

## Instalación y validación

`gateway/pi-service/install_mesh.sh` actualiza el servicio y el visor, conserva una copia previa y comprueba tres respuestas HTTP del mismo PID del visor. También retira las utilidades de flasheo del gateway y sus permisos web. `server/install_mesh_metrics.sh` detecta el consumidor, conserva sus archivos y un volcado de PostgreSQL, aplica la migración 007 e instala el consumidor ampliado. Los secretos se leen de la configuración operativa existente y no forman parte de los paquetes.

La configuración celular requiere compilar y cargar el firmware 0.0.86 en el supernodo, el nodo destino y todos los relés. Una prueba nativa con dependencias simuladas comprueba el encaminamiento y las identidades; no demuestra cobertura radio, comportamiento del módem físico ni actualización ESP32. Permanecen pendientes las lecturas y escrituras reales por NB-IoT directo y mediante relés, los cortes de ruta durante una operación, las actualizaciones LoRa con ambos valores del permiso y la persistencia con los servicios desplegados.
