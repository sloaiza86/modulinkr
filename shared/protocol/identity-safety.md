# Identidad física y aislamiento de la red

El código del nodo 0.0.83, el gateway y el consumidor utiliza el protocolo de radio 4.0. El identificador numérico continúa sirviendo para direccionar; la identidad física distingue los equipos que comparten ese número. La definición de la cabecera se encuentra en [`frame-format.md`](frame-format.md), §27.

## Protección y comportamiento visible

La configuración por USB consulta un HELLO nuevo, obtiene el UID del equipo y reserva el ID en una transacción del gateway. Se utiliza la misma comprobación al conectar el nodo al gateway y al configurarlo con Web Serial. La reserva permanece aunque el equipo esté apagado. Un JSON de otro despliegue se rechaza. La configuración remota no puede cambiar la identidad numérica ni el despliegue.

Si una configuración duplicada elude esos controles, la primera trama recibida de otro UID con el mismo ID abre una incidencia persistente. Se bloquean sus nuevas muestras, cambios de catálogo y órdenes. Se conserva la evidencia en cuarentena; los contenidos binarios se registran por hash para evitar copiar configuraciones con secretos. El conflicto permanece después de reiniciar el gateway. Leer la notificación no lo resuelve.

El visor muestra los equipos observados y permite resolver el conflicto tras confirmar que se desconectó el duplicado o se cambió su ID. Se registra el motivo, se retira el estado operativo anterior y se exige nueva actividad y catálogo del equipo conservado. Si el otro equipo vuelve a emitir con ese ID, reaparece el bloqueo. Una segunda autoridad con dirección 255 bloquea la red; la resolución solo puede conservar el gateway que sirve el visor. Después se reinician los nodos que hayan retenido ese conflicto.

El control no descubre un equipo apagado que nunca anunció su identidad. La comprobación tampoco demuestra autenticidad individual frente a alguien que conoce la clave compartida y falsifica el UID. Con seguridad de radio desactivada, la identidad declarada no tiene protección criptográfica.

## Criticidades cubiertas

| Situación | Protección |
| --- | --- |
| IDs de nodo repetidos | Reserva persistente, detección por UID, bloqueo y resolución auditada |
| Dos gateways en la misma red | Autoridad física conservada en el nodo y bloqueo global en el gateway |
| Clientes MQTT que se expulsan | Clientes del gateway por UID, sondas con UUID y consumidor por host, broker y base |
| IDs iguales en despliegues distintos | Topics `modulinkr/v2/<UID del gateway>/<publicador>/...` y esquema PostgreSQL `ml_<UID>` |
| Reinicio, vuelta de secuencia o retroceso del reloj | Sesión persistida antes de transmitir, contador de cifrado por salto y aviso de retroceso |
| ACK atrasado de otra sesión | Confirmación con equipo, sesión, secuencia y timestamp de captura |
| Backlog tras cambiar una medida | Huella del catálogo en cada muestra y catálogos inmutables conservados |
| Misma identidad de muestra con contenido distinto | Valor original conservado e incidencia de conflicto |
| Orden celular dirigida a otro equipo | UID del receptor celular, UID del destino final y despliegue comprobados |
| Direcciones Modbus repetidas en la configuración | Rechazo en formulario y analizador del firmware dentro del mismo bus |
| Binario de desarrollo fuera del contrato actual | Rechazo al distribuir por USB o radio y revisión de consistencia del paquete |

Las direcciones Modbus físicas duplicadas no siempre pueden distinguirse: dos dispositivos idénticos pueden producir respuestas indistinguibles. La configuración se valida, pero la incorporación de equipos que comparten la dirección de fábrica requiere comisionarlos por separado.

El nonce AES-CCM utiliza el UID del transmisor del salto, una sesión persistida y un contador de 24 bits. Se autentica toda la cabecera y el timestamp de seguridad. El nodo reserva sesiones en NVS. La escritura USB excluye la partición NVS comprobada en la imagen, de `0x9000` a `0xDFFF`, tanto en el gateway como en Web Serial. `eraseAll:false` por sí solo no preservaba esa región, porque la imagen completa incluía los huecos entre particiones. Se rechaza una geometría distinta antes de escribir. El gateway conserva el contador de sesiones en `.modulinkr-radio-sessions.sqlite`, junto al buffer y separado de sus restauraciones. Borrar o restaurar conjuntamente esos contadores permite reutilizar sesiones; en ese caso se requiere cambiar la clave de red antes de volver a transmitir.

## Primera publicación y conservación interna del banco

ModuLinkr sigue en desarrollo y todavía no ha publicado su primera versión. Todos los despliegues reales partirán con identidad física. La radio admite exclusivamente `0x40`, la configuración 3.3 y el consumidor MQTT 4.0 en `modulinkr/v2`. No se mantienen entradas operativas para los formatos previos del desarrollo.

El visor retira «Histórico anterior a la identidad física» y los avisos `legacy_pending` y `unverified`, incluidas las notificaciones persistidas por esa implementación. Esos avisos describían mantenimiento interno del banco, sin una acción útil para el usuario. Los conflictos reales de nodo o gateway siguen visibles y requieren resolución explícita.

Los archivos de outbox de otro formato se archivan fuera de la cola operativa. El gateway conserva las tablas internas y aparta las filas y catálogos de prueba sin UID del buffer activo. PostgreSQL conserva sus estructuras base y los datos internos, pero el visor consulta únicamente el esquema del gateway actual. No se borra el histórico interno durante la corrección.

Se conserva el histórico normal del producto: versiones de catálogo, medidas anteriores, cambios de configuración, recuperación ante fallos y futuras actualizaciones. Las migraciones de almacenamiento aplicadas permanecen inmutables. La numeración interna no acredita una base instalada de clientes.

El código declara 0.0.83; las imágenes locales siguen en 0.0.82 hasta realizar Build y empaquetado. El paquete completo exige coincidencia entre la versión del código y la imagen. El paquete de servicios y GUI no incluye firmware y permite corregir la interfaz del banco actual antes de compilar.

## Instalación y comprobación de banco

### Servicios y GUI, sin compilar firmware

El paquete actualiza el servicio y el visor; retira las opciones y avisos incorrectos sin copiar imágenes. Se actualiza también el consumidor para retirar la ingesta anterior y procesar salud 4.0. En el Mac:

```bash
python3 "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/shared/protocol/package_identity.py" server /private/tmp/modulinkr-identity-server.tar.gz
scp /private/tmp/modulinkr-identity-server.tar.gz modulinkr@74.161.32.30:/tmp/modulinkr-identity-server.tar.gz
python3 "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/shared/protocol/package_identity.py" gateway-runtime /private/tmp/modulinkr-prepublic-gateway.tar.gz
scp /private/tmp/modulinkr-prepublic-gateway.tar.gz modulinkr@Gateway.local:/tmp/modulinkr-prepublic-gateway.tar.gz
```

En la sesión Termius de la VM:

```bash
mkdir -p /tmp/modulinkr-identity-server
tar -xzf /tmp/modulinkr-identity-server.tar.gz -C /tmp/modulinkr-identity-server
sudo bash /tmp/modulinkr-identity-server/server/install_identity.sh
sudo systemctl status modulinkr-consumer.service --no-pager
sudo journalctl -u modulinkr-consumer.service -n 80 --no-pager
```

En la sesión Termius del gateway:

```bash
mkdir -p /tmp/modulinkr-prepublic-gateway
tar -xzf /tmp/modulinkr-prepublic-gateway.tar.gz -C /tmp/modulinkr-prepublic-gateway
bash /tmp/modulinkr-prepublic-gateway/gateway/pi-service/install_identity.sh --runtime-only
sudo systemctl status modulinkr-gateway.service modulinkr-web.service --no-pager
sudo journalctl -u modulinkr-gateway.service -u modulinkr-web.service -n 80 --no-pager
```

Se recarga el visor con `Cmd+Shift+R` para solicitar los recursos `prepublic-20261014`. Se comprueba que desaparecen el aviso del nodo 0, las notificaciones asociadas y el selector de histórico sin identidad. Las medidas del despliegue actual y los conflictos reales deben seguir disponibles.

### Firmware completo 0.0.83

El banco ya dispone de imágenes 0.0.82 con identidad física. Para aplicar las restricciones nuevas del analizador del nodo y retirar los informes destinados a gateways internos anteriores, se compila `nodo` en VS Code mediante `Cmd+Shift+P`, `PlatformIO: Build`. Tras un Build correcto, en el Mac:

```bash
cd "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/nodo" && ./make_dist.sh
python3 "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/shared/protocol/package_identity.py" gateway /private/tmp/modulinkr-identity-gateway.tar.gz
scp /private/tmp/modulinkr-identity-gateway.tar.gz modulinkr@Gateway.local:/tmp/modulinkr-identity-gateway.tar.gz
```

En la sesión Termius del gateway:

```bash
mkdir -p /tmp/modulinkr-identity-gateway
tar -xzf /tmp/modulinkr-identity-gateway.tar.gz -C /tmp/modulinkr-identity-gateway
bash /tmp/modulinkr-identity-gateway/gateway/pi-service/install_identity.sh
sudo systemctl status modulinkr-gateway.service modulinkr-web.service --no-pager
sudo journalctl -u modulinkr-gateway.service -u modulinkr-web.service -n 80 --no-pager
```

La copia de imágenes al gateway no instala los nodos. Se utiliza la actualización normal del visor o `PlatformIO: Upload` desde VS Code y después `PlatformIO: Monitor`. Se revisa un único equipo por USB cada vez. Se verifica registro, configuración 3.3, medidas, disponibilidad y confirmación OTA con transferencia y hash de imagen.

El instalador del gateway detecta las rutas operativas, guarda una copia de SQLite y de los archivos, y exige tres respuestas correctas de `/login` desde el mismo PID. Restaura código y buffer si falla y conserva además `failed-buffer.db` con cualquier muestra recibida durante el intento. Esa copia requiere recuperación supervisada antes de retirarla. El contador separado de sesiones no se restaura. El instalador de la VM guarda una copia PostgreSQL protegida, aplica la migración 006 y concede el permiso de creación de esquemas al usuario operativo. Ante fallo de arranque restaura el código; conserva los nuevos esquemas para no borrar datos recibidos.

Se requiere que las ACL del broker permitan publicar y suscribir el espacio `modulinkr/v2` para los usuarios operativos. La migración conserva el rol de lectura `modulinkr_ro`; los roles adicionales requieren figurar en `public.deployment_readers` antes de crear un despliegue. No se modifican contraseñas ni ACL durante estos instaladores.

La comprobación física pendiente incluye dos equipos con el mismo ID, reinicio del gateway con el conflicto abierto, retirada y resolución del duplicado, nueva recepción del catálogo, una orden con UID ajeno, muestras guardadas antes de un cambio de catálogo, custodia por relay, entrega NB-IoT y permisos PostgreSQL y MQTT reales. Las pruebas locales no acreditan ese comportamiento en radio ni el funcionamiento de la versión de librerías instalada en los equipos.

## Validación local

La evidencia del barrido, el inventario y las comprobaciones se mantienen en [`prepublic-audit.md`](prepublic-audit.md). Las pruebas nativas, SQLite, PostgreSQL temporal y Chrome con respuestas simuladas no acreditan despliegue, compilación ESP32 ni funcionamiento en radio o hardware.
