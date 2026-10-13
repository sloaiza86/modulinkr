# Configuraciones de banco y prueba de escrituras

Se dispone de configuraciones para el nodo con XY-MD02 y el supernodo con WT901C485. Los archivos `nodo1.json` y `nodo2.json` conservan su contenido anterior. Las plantillas de escritura añaden acciones y lecturas de comprobación, con cuatro acciones por dispositivo, sin cambiar la dirección Modbus ni los 9600 baud del banco.

La plantilla ambiental se ajusta a la configuración operativa aportada en 2026-10-12: nombre `Nodo Sensor Ambiental`, dispositivo `Sensor Ambiental`, TTL 6, intervalo de envío de 10000 ms y diagnóstico Modbus `all_each`. Se conservan todos sus campos, incluida la clave de la red de desarrollo, y solo se añaden dos lecturas y cuatro escrituras. La plantilla del supernodo se ajusta a la exportación operativa `config-2-supernodo-acelerometro.json` aportada en 2026-10-13. Se conservan identidad, parámetros de transporte y las tres lecturas de aceleración. Solo se añaden dos lecturas de comprobación y cuatro escrituras.

| Plantilla | Destino | Lecturas | Escrituras |
| --- | --- | --- | --- |
| [`nodo1-escrituras-xymd02.json`](nodo1-escrituras-xymd02.json) | Nodo 1, XY-MD02, esclavo 1 | Temperatura, humedad y ambas correcciones | 0x06 y 0x10, uno y dos registros |
| [`nodo2-escrituras-wt901c485.json`](nodo2-escrituras-wt901c485.json) | Supernodo 2, WT901C485, esclavo 80 | Aceleración XYZ, filtro y orientación | 0x06, desbloqueo, filtro, orientación y guardado |

## Carga desde el visor

El archivo operativo del supernodo, `/private/tmp/config-2-supernodo-acelerometro-escrituras.json`, conserva íntegramente la configuración de transporte exportada, incluida la clave LoRa y los datos APN y MQTT. Se importa ese archivo completo para evitar que una omisión de campos genere diferencias de red. Se almacena fuera del repositorio con permisos 0600. La plantilla del supernodo dentro del repositorio conserva los parámetros no secretos y omite la clave y las credenciales; no sustituye al archivo operativo para una importación completa. La seguridad LoRa permanece habilitada.

1. Abrir el configurador del visor y cargar la configuración actual del destino. Comprobar que los ajustes de red proceden del gateway y que el supernodo conserva sus datos APN y MQTT.
2. Importar el archivo operativo completo del destino. Para el supernodo se utiliza `/private/tmp/config-2-supernodo-acelerometro-escrituras.json`. El formulario conserva la clave LoRa cargada desde el gateway y los campos de credenciales omitidos. Si aparece el aviso de red, seleccionar **Usar ajustes actuales**. Revisar los datos APN y MQTT antes de aplicar.
3. Comprobar el ID, el tipo de nodo y la dirección del dispositivo. Aplicar la configuración desde el visor al destino seleccionado.
4. Esperar el catálogo nuevo y comprobar las cuatro acciones en **Operaciones**. La importación del archivo declara acciones; no ejecuta escrituras.

La plantilla ambiental coincide con todos los parámetros de transporte de la exportación, incluida la clave. El archivo operativo del supernodo también conserva íntegramente el transporte de su exportación. Si se utiliza en su lugar la plantilla del repositorio, el aviso de importación puede mencionar la clave omitida aunque el formulario conserve la operativa. Ese aviso no prueba que se haya cambiado la clave del formulario. El botón **Usar ajustes actuales** cierra el aviso y el código actual no repone el TTL que haya sobrescrito la importación. Por eso el TTL debe coincidir ya en la plantilla o corregirse en el formulario antes de aplicar.

Se requiere el firmware con control remoto y fragmentación corregida, declarado actualmente como 0.0.81, y el servicio y visor compatibles con el catálogo 0x3B. La carga de configuración por radio conserva el mecanismo existente; estas plantillas no habilitan su transferencia por NB-IoT.

## XY-MD02

Se utilizan los registros de corrección de temperatura, 0x0103 (259 decimal), y humedad, 0x0104 (260 decimal). Cada corrección es un entero con signo en décimas, con intervalo documentado de -10 a +10 en su unidad. Las acciones individuales aceptan el valor físico, por ejemplo `1` para +1 °C, y el firmware aplica la escala inversa. Las direcciones son PDU, sin sumar ni restar uno. La tabla y las funciones se documentan en el [manual XY-MD02, páginas 3 y 6](https://knowledgebase.grenton.com/hubfs/xy-md02-manual.pdf).

| Acción | Función | Registro | Valor de prueba en Control | Lectura de comprobación |
| --- | --- | --- | --- | --- |
| `tc06` | 0x06 | 259 | `1`, después `-1` | `ctemp`: +1 °C, después -1 °C |
| `hc06` | 0x06 | 260 | `1`, después `-1` | `chum`: +1 %RH, después -1 %RH |
| `tc10` | 0x10, cantidad 1 | 259 | `0.5`, después `-0.5` | `ctemp`: +0,5 °C, después -0,5 °C |
| `th10` | 0x10, cantidad 2 | 259 y 260 | `720886` | `ctemp`: +1 °C; `chum`: -1 %RH |
| `th10` | 0x10, cantidad 2 | 259 y 260 | `4294311946` | `ctemp`: -1 °C; `chum`: +1 %RH |

Antes de escribir, se anotan ambas correcciones actuales para restaurarlas al terminar. Una escritura sustituye la corrección anterior, no añade otra corrección a la guardada. La lectura del registro permite verificar el cambio sin atribuir a la orden una variación ambiental de temperatura o humedad.

`th10` empaqueta los dos registros en un `uint32` con orden `ABCD`: los 16 bits altos contienen temperatura y los bajos humedad. El sensor sigue teniendo dos enteros de 16 bits independientes. Se emplea este empaquetado porque el ejecutor actual admite un valor numérico por acción y dos registros para un escalar de 32 bits. Así se prueba una transacción 0x10 de dos registros y el transporte de un entero mayor que 2^31. Para valores brutos `T` y `H`, el número es `((T & 65535) << 16) | (H & 65535)`; ambos valores brutos deben permanecer entre -100 y +100. Los números de la tabla ya están calculados.

La restauración se realiza con `tc06` y `hc06`, utilizando las correcciones físicas anotadas. Si ambas eran cero, se escribe `0` en cada acción. No se utiliza el máximo uint32 como prueba física: excedería las correcciones permitidas del sensor.

## WT901C485

Las acciones utilizan 0x06. El [SDK oficial específico del WT901C485](https://github.com/WITMOTION/WitStandardModbus_WT901C485/blob/main/STM32F1/STM32Core_SDK_modbus/Source/wit_c_sdk/wit_c_sdk.c) desbloquea antes de cambiar el filtro. El [mapa de registros del fabricante](https://wit-motion.gitbook.io/witmotion-sdk/wit-standard-protocol/wit-standard-communication-protocol) define el desbloqueo, guardado y los códigos siguientes. El [manual WT901C RS485, §3.4.3 y §3.4.6](https://cdn.robotshop.com/rbm/f83835f4-5e29-4ee0-9cc2-e49300031503/5/53734f08-7d97-497f-87d3-5f2e8c31c91c/a0fe5b4c_wt901c-rs485-manual.pdf) describe orientación y ancho de banda.

| Acción | Registro PDU decimal | Valor en Control | Significado |
| --- | --- | --- | --- |
| `unlock` | 105 (0x0069) | `46472` | Desbloqueo, valor público 0xB588 del protocolo |
| `band` | 31 (0x001F) | `6`, después el valor original | Filtro de 5 Hz; `4` corresponde a 20 Hz |
| `orient` | 35 (0x0023) | `1`, después el valor original | Montaje vertical; `0` corresponde a horizontal |
| `save` | 0 (0x0000) | `0` exclusivamente | Guardar los ajustes actuales |

Se anotan primero `bandcfg` y `orcfg`. Para cambiar un parámetro se envía `unlock`, se espera su resultado, se envía el parámetro y se comprueba su lectura. El guardado requiere mantener el sensor desbloqueado; si venció la ventana, se desbloquea de nuevo antes de `save`. Al terminar se restauran los valores anotados y se guardan. Guardar con `0` conserva todos los ajustes actuales del sensor, no solo el último parámetro escrito. Otros valores del registro SAVE pueden reiniciar o restaurar el sensor y no forman parte de esta prueba.

Los códigos válidos del filtro son `0` para 256 Hz, `1` para 188 Hz, `2` para 98 Hz, `3` para 42 Hz, `4` para 20 Hz, `5` para 10 Hz y `6` para 5 Hz. La GUI recibe el código, no la frecuencia. No se escribe `20` para seleccionar 20 Hz. La orientación se prueba por lectura del registro; el banco actual publica aceleración XYZ, sin añadir lecturas de ángulos.

El fabricante documenta un bloqueo automático tras 10 segundos. La implementación actual envía acciones independientes y no agrupa desbloqueo, cambio y guardado dentro del supernodo. La latencia de radio y el respaldo celular pueden agotar esa ventana. Estas acciones permiten probar el envío 0x06 y contrastar los registros, pero el archivo de configuración no garantiza una secuencia de configuración eficaz por NB-IoT. Una respuesta Modbus confirmada se contrasta siempre con `bandcfg` u `orcfg`; el funcionamiento de la ventana y la persistencia requieren prueba física.

## Cobertura y resultado esperado

Se prueba primero con LoRa disponible. Después se repite con LoRa hacia el gateway no disponible, manteniendo accesible el tramo entre nodo y supernodo y la conexión celular del supernodo. El visor conserva la misma acción y selecciona automáticamente el respaldo NB-IoT. Al recuperar LoRa, una operación nueva debe volver a utilizarlo.

El resultado del comando, la lectura posterior del registro y el efecto en la medida son evidencias distintas. La ausencia de respuesta deja un resultado incierto, aunque el dispositivo pudiera haber escrito. No se repite una orden incierta sin comprobar primero el registro.

Las plantillas cubren 0x06, 0x10, signo, escala, uno y dos registros, `uint16`, `int16`, `uint32` y orden `ABCD`. Los manuales no documentan bobinas para estos equipos; 0x05 y 0x0F requieren otro dispositivo o un esclavo simulado. Tampoco se acredita con este banco la escritura de `float32`, `int32` ni los otros órdenes de bytes.

La comprobación nativa de ambas plantillas utiliza el parser real de configuración, el constructor del catálogo del nodo y su decodificador del gateway, con una clave ficticia en memoria. Los catálogos ocupan 416 bytes para el XY-MD02 y 339 para el WT901C485. Se verifican 16 transacciones del ejecutor y controlador reales sobre un bus simulado: diez del XY-MD02 y seis del WT901C485, incluidos valores negativos, escala, cantidad y el par de 32 bits. Se comprueba la validación real del formulario y, de forma aislada, la conservación de sus campos de credenciales omitidos al rellenarlo. Tras incorporar la clave a la plantilla ambiental, se verifica la igualdad de todos los parámetros de transporte con la exportación y la ausencia de avisos en los comparadores reales del visor, usando esa exportación como referencia. Esta comprobación no incluye una importación visual en navegador ni una consulta de los ajustes del gateway desplegado.

La preparación y las comprobaciones locales no acreditan PlatformIO Build, Upload, despliegue, aceptación del sensor ni el respaldo por radio real.

## Presentación en Operaciones

Con firmware 0.0.81, las dos correcciones ambientales aparecen como operaciones editables, con último valor leído y nuevo valor separados. Las variantes de temperatura 0x10 y el par bruto permanecen en Pruebas Modbus. Se conserva la identidad y la red de la plantilla ambiental existente; los campos nuevos describen únicamente la presentación de las escrituras.

La plantilla del acelerómetro presenta el filtro como selector de frecuencia y la orientación como Horizontal o Vertical. El desbloqueo y el guardado son botones con valores fijos. Seleccionar 20 Hz envía el código 4. Se conserva la ejecución individual: desbloqueo, modificación y guardado siguen siendo órdenes independientes. La ventana de diez segundos del dispositivo no queda automatizada por este cambio y debe comprobarse en banco, especialmente con el respaldo celular.
