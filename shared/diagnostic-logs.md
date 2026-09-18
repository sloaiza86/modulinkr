# Registros de diagnóstico

El gateway, el visor, la radio Heltec y el firmware común de nodos y supernodos generan registros técnicos en inglés. La presentación sigue los criterios de claridad y consistencia de [Command Line Interface Guidelines](https://clig.dev/) y toma del [modelo de registros de OpenTelemetry](https://opentelemetry.io/docs/specs/otel/logs/data-model/) la separación entre tiempo, severidad, origen, evento y atributos. Se adopta su modelo conceptual; no se incorpora un SDK, un colector ni un protocolo de exportación OpenTelemetry.

## Formato y tiempo

Cada registro de aplicación ocupa una línea de texto. Las primeras columnas reservan 20 caracteres para el tiempo, ocho para el nivel y 24 para el componente. El evento usa un nombre estable; los valores variables quedan después, como campos `clave=valor`. Los textos de detalle pueden contener espacios, por lo que no se declara compatibilidad completa con un analizador logfmt.

```text
2026-09-08T09:30:00Z INFO     modulinkr.gateway        event=radio.opened port=/dev/ttyUSB0 baud=115200
up=0000000123.456s   INFO     node.mesh                event=mesh.parent_changed from=2 to=255 own_hop=1
up=0000000125.100s   ERROR    node.nbiot               event=nbiot.mqtt_connection_failed response=timeout
```

Los servicios usan UTC en ISO 8601. El nodo usa el reloj de red existente cuando está sincronizado, con resolución de segundos. Antes de sincronizarse, y en la radio sin reloj UTC, `up=` indica segundos y milisegundos desde el arranque obtenidos del contador monotónico de 64 bits del ESP32. Ese valor no es una fecha y no permite comparar directamente el instante de dos equipos distintos. El monitor PlatformIO no añade otra hora a la izquierda.

## Niveles y transporte

`DEBUG` identifica tramas, muestras y contadores de diagnóstico; `INFO`, actividad y cambios de estado; `WARNING`, anomalías o recuperación; `ERROR`, operaciones fallidas; `CRITICAL`, fallos que impiden continuar el arranque de la radio. El nivel se define en cada emisor, no se deduce de palabras que aparezcan en los datos. Los controles existentes de diagnóstico Modbus y del módem se conservan. El filtro web solo oculta registros recibidos; no activa trazas en el dispositivo ni reduce el tráfico serie.

El emisor C++ construye cada registro en un búfer acotado y lo entrega con una llamada a `Serial.write`, para evitar que los mensajes de las tareas de LoRa y NB-IoT se mezclen entre llamadas parciales. Los caracteres de control presentes en los valores se sustituyen por espacios. Si un registro excede su capacidad, termina con `truncated=true`. Las tramas Modbus de diagnóstico incluyen longitud y hexadecimal en un solo registro. Se mantiene la ocultación de las credenciales del comando de conexión MQTT.

El servicio del gateway conserva el nivel y el evento del diagnóstico estructurado del Heltec. El tiempo y el componente originales se añaden como `source_time` y `source_component`, separados de la fecha UTC del registro del servicio. Por SSH, el texto de `MESSAGE` puede consultarse con `journalctl -u modulinkr-gateway.service -o cat`; la anchura de la terminal determina si las líneas se ajustan visualmente. En una consulta paginada, `less -S` permite desplazarse horizontalmente.

## Contratos y límites

Las respuestas `CFG:*`, las líneas `[rx]` y `[tx]`, la respuesta `[fw] version=...` y la identidad de arranque de la radio conservan su formato porque otras herramientas las interpretan. El marcador `modbus-debug origin=... mode=...` también permanece en el journal, acompañado del evento `modbus.frame`. Los mensajes de ROM, bibliotecas y herramientas externas quedan fuera del emisor de aplicación. No se les asigna una severidad inventada en el visor.

Los registros no modifican tramas LoRa, consultas al módem, temporizadores de red ni configuración persistida. El cambio añade trabajo de formato y bytes al puerto de diagnóstico; su impacto en el banco requiere comprobación con el firmware compilado.

## Visor web

Las tres pestañas comparten fuente monoespaciada de 13 píxeles, una línea por registro y desplazamiento horizontal. Los registros técnicos se marcan con `translate="no"` para evitar que el navegador cambie eventos o campos. Las advertencias y errores se resaltan conservando su etiqueta textual.

El nivel mínimo, el componente y la búsqueda se combinan sobre los últimos 800 registros recibidos. Cambiar un filtro no elimina datos del búfer. Las líneas antiguas o de protocolo siguen visibles en «Todos los niveles»; al seleccionar un nivel mínimo se muestran únicamente registros con severidad explícita. El monitor USB del gateway acumula fragmentos hasta recibir el salto de línea, igual que el monitor local, incluso si una lectura serie vence a mitad de un carácter UTF-8.

## Versiones y comprobación

El cambio se distribuye con el nodo 0.0.61 y la radio 0.3.3. Las comprobaciones de escritorio cubren formato, tiempo UTC y monotónico, sustitución de controles, truncamiento, escritura por registro, conservación de severidad al recibir del Heltec, respuestas protegidas, reensamblado USB y filtros del visor. La revisión visual usa registros simulados. Quedan pendientes la compilación en PlatformIO, la instalación y la comprobación física por USB, SSH y web.
