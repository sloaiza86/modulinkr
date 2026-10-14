# ModuLinkr, órdenes remotas

La implementación actual permite escrituras Modbus declaradas por el catálogo y protegidas por identidad física. ModuLinkr sigue en desarrollo antes de la primera publicación. Los diseños internos anteriores de comandos generales no son formatos admitidos ni capacidades disponibles.

Se admiten las funciones 0x05, 0x0F, 0x06 y 0x10. Los comandos generales `read`, `reboot`, `reload_config`, `flush_batch`, `test_batch` y `get_status` de la propuesta inicial no se implementan por este canal. El mantenimiento y la configuración utilizan sus APIs y tramas específicas.

## Escrituras implementadas

El visor registra la orden mediante `POST /api/comandos/nodos/{origin}`, con dispositivo, identificador de acción y valor. El servicio del gateway la entrega por LoRa y recoge el resultado. Tras tres intentos sin resultado, o cuando no existe una ruta LoRa compatible, utiliza automáticamente el supernodo de la ruta celular observada. No existe selector de transporte en la GUI. Una operación que ya ha pasado a NB-IoT conserva esa vía hasta terminar; las operaciones nuevas vuelven a preferir LoRa cuando se recupera.

La orden caduca a los 300 s. Los envíos se separan al menos 10 s y conservan identificador, vencimiento, firma de acción y contenido. El gateway espera 30 s adicionales para recibir una respuesta antes de declarar ausencia de confirmación. La operación y su historial viven en `modbus_commands`, dentro del buffer operativo SQLite. El visor admite una sola operación activa por destino. Las transferencias de configuración, las migraciones, las ventanas de silencio y la difusión de firmware tienen prioridad sobre el envío de escrituras.

El mensaje MQTT utiliza el topic `modulinkr/v2/<deployment_id>/{supernodo}/cmd`, QoS 1 y sin retención. Contiene `schema_version: "4.0"`, el recorrido `path` desde el supernodo hasta el destino y `body`, la petición binaria codificada en hexadecimal. La petición se define en [`frame-format.md`](frame-format.md) §26. El supernodo recibe el mensaje dentro de la tarea del módem y lo entrega al bucle principal mediante una cola. Para un nodo sin módem, reenvía la misma petición por el recorrido LoRa inverso observado en la custodia.

El resultado vuelve por el recorrido inverso. Si termina en el gateway se procesa por LoRa; si termina en el supernodo, se publica en `modulinkr/v2/<deployment_id>/{supernodo}/cmd/response`, QoS 1 y sin retención. El JSON contiene `schema_version`, `origin`, `id`, `fingerprint`, `status`, `modbus_status` y `exception`. El gateway comprueba destino, identidad de operación, huella de petición y publicador celular antes de actualizar el historial. El PUBACK solo confirma entrega al broker.

Los registros reciben un valor numérico escalar, incluso cuando ocupa dos registros. La conversión inversa aplica `(value - offset) / scale`; se rechazan valores no finitos, fuera de rango o que requieran truncar un entero. Las bobinas reciben un booleano o un array de la cantidad declarada, con un máximo de 125. La función 0x0F se mantiene aunque la cantidad sea uno. La firma anunciada por acción impide ejecutar una orden preparada para otra dirección, tipo, cantidad o conversión.

Los errores de comunicación Modbus posteriores al envío se muestran como resultado incierto porque el dispositivo pudo aplicar la escritura y perderse su respuesta. Una excepción Modbus válida se muestra como rechazo. El nodo persiste la operación antes de tocar el bus y conserva el último resultado. Un duplicado no repite la transacción. Si el nodo reinicia con una operación persistida en curso, devuelve incertidumbre. Una petición anterior al último identificador guardado tampoco se ejecuta y se declara incierta porque no se conserva su resultado individual. La confirmación Modbus acredita la respuesta del dispositivo, no la actuación física de un relé o mecanismo.

La recepción del SIM7028 utiliza las longitudes anunciadas en CMQTTRXSTART, CMQTTRXTOPIC y CMQTTRXPAYLOAD, incluidos los bloques fragmentados. El contenido recibido queda separado de los terminadores de las respuestas AT y no se pierde al consultar el estado del módem o publicar telemetría. La secuencia de suscripción se comprueba contra la nota MQTT(S) V1.03 del SIM7028. Permanecen pendientes la compilación ESP32 y la validación del módem, del bus y de ambos recorridos en el banco real.


## Identidad del sobre 4.0

La orden celular exige `schema_version: "4.0"`, `deployment_id`, `destination_uid` del receptor celular y `target_uid` del destino final. `path` describe el recorrido y `body` contiene la petición binaria. El receptor verifica versión, despliegue y ambos destinos antes de ejecutar o reenviar. La respuesta contiene despliegue, UID del equipo que publica y su sesión; el gateway comprueba la asignación física y la correlación de la operación.

Los temas se derivan de la identidad del gateway y del publicador; no se editan como parámetros del nodo. Una coincidencia de ID numérico no autoriza ejecutar una orden para otro equipo.
