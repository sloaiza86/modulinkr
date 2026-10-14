# ModuLinkr Server Installer

Instalador del servidor cloud de ModuLinkr. Instala tres componentes, el broker MQTT (Mosquitto con TLS), la base de datos (PostgreSQL con el esquema de telemetría) y el consumidor cloud (servicio de ingesta MQTT a PostgreSQL, ver [`consumer/README.md`](consumer/README.md)), por separado o juntos. Está pensado para una VM Ubuntu de poca memoria y para ser reejecutable sin romper una instalación previa.

## Uso

```bash
sudo ./install.sh                          # menú interactivo
sudo ./install.sh --components database     # solo la base de datos
sudo ./install.sh --components all -y       # todo, sin preguntas
sudo ./install.sh --config modulinkr-server.conf --components all
```

El menú pregunta qué instalar (`broker`, `database`, `consumer` o `all`) y luego los detalles de cada componente. Con `--components` se salta el menú, y con `-y` se corre sin preguntas tomando defaults y lo que traiga el config.

## Componentes

El componente `database` instala PostgreSQL 16 (con la extensión TimescaleDB si `ENABLE_TIMESCALEDB=1`), crea el rol y la base de la aplicación, ajusta la memoria para la VM, configura swap, aplica las migraciones del esquema y concede al rol de la aplicación los permisos sobre el esquema. El componente `broker` instala Mosquitto, provisiona el certificado TLS (Let's Encrypt con dominio, autofirmado RSA sin él), configura el listener TLS en 8883 con autenticación por usuario y contraseña, y deja el servicio bajo systemd. El componente `consumer` despliega el servicio de ingesta en `/opt/modulinkr/consumer` (venv con paho-mqtt y psycopg2, usuario de sistema propio, unidad systemd) y reutiliza las credenciales de la base de `database.env`; en la instalación `all` va el último por esa razón.

## Broker MQTT y TLS

El broker se identifica por dominio o por IP, según lo que tenga el despliegue, y de esa elección depende la estrategia de certificado. Con dominio, el instalador emite un certificado Let's Encrypt con certbot (modo standalone, usa el puerto 80), lo despliega a la carpeta de Mosquitto e instala un hook que, en cada renovación, vuelve a copiarlo y recarga el servicio. Sin dominio, genera un certificado autofirmado RSA (clave RSA, no ECDSA, porque el SIM7028 no negocia ECDSA) y avisa en pantalla de que no es de confianza pública.

El certificado vive solo en el servidor. Los nodos SIM7028 establecen el TLS sin verificar el certificado del servidor (`authmode=0`, ver `node-config.md` nota v2.3), así que no cargan ninguna CA: el certificado existe únicamente porque Mosquitto necesita uno para servir TLS. Por eso el autofirmado sirve igual que el de Let's Encrypt para los nodos; la diferencia es que Let's Encrypt es de confianza pública y se renueva solo, y solo funciona con dominio. La identidad del nodo va por usuario y contraseña MQTT, no por certificado de cliente. Con dominio, certbot exige el puerto 80 accesible desde internet y el DNS del dominio apuntando a la VM.

## Reproducibilidad e idempotencia

El instalador se apoya en un archivo de configuración y en control de estado para dar el mismo resultado en cada ejecución. Los repositorios de paquetes, el rol, la base, el ajuste de memoria y el swap solo se crean si faltan, así que reejecutar es seguro. La contraseña de la base, si no se fija en el config, se genera una vez y se guarda en `/etc/modulinkr/database.env` (solo root); las pasadas siguientes la reutilizan desde ahí en vez de generar una nueva.

Para clonar una instalación en otra máquina basta con copiar `config/modulinkr-server.conf.example` a `modulinkr-server.conf`, rellenar los valores y correr el instalador con `--config`.

## Credenciales

Las contraseñas no se guardan en el repositorio ni se pasan por el config. El usuario y la contraseña MQTT se preguntan durante la instalación, con la contraseña pedida dos veces para confirmar que coinciden. La contraseña de la base se genera sola y se guarda solo en `/etc/modulinkr/database.env` (permisos de root). El `config` solo lleva ajustes no sensibles (versión, nombres, puertos, tuning); un `modulinkr-server.conf` rellenado está en `.gitignore`.

## Migraciones del esquema

El esquema vive en `db/migrations` como archivos SQL numerados (`001_init.sql`, `002_...`). El runner `db/apply_migrations.sh` aplica en orden las que aún no constan en la tabla `schema_migrations` y salta las ya aplicadas. Cada archivo controla su propia transacción. Las migraciones son inmutables: un cambio de esquema se hace con un archivo nuevo, no editando uno ya aplicado (el runner avisa si detecta que el contenido de una migración aplicada cambió).

El runner corre solo o desde el instalador:

```bash
MODULINKR_PSQL="sudo -u postgres psql" ./db/apply_migrations.sh --db modulinkr
```

El esquema (`001_init.sql`) es la contraparte de almacenamiento de [`../shared/protocol/db-schema.md`](../shared/protocol/db-schema.md): tablas `nodes`, `channels`, `samples`, `sample_values` y `quarantine`, con los índices únicos que materializan las identidades de deduplicación de `batch-format.md` §8.1.

## Ajuste de memoria

El módulo escribe un drop-in en `conf.d/99-modulinkr.conf` en vez de tocar el `postgresql.conf` del paquete, con valores conservadores para 1 GB de RAM compartida con Mosquitto (`shared_buffers`, `effective_cache_size`, `work_mem`, `maintenance_work_mem`, `max_connections`). Todos son configurables desde el config. Cuando TimescaleDB está activo, el mismo drop-in añade `shared_preload_libraries = 'timescaledb'`, que Postgres necesita al arrancar para poder crear la extensión. El swap (2 GB por defecto) actúa de colchón ante picos con la base y el broker en la misma máquina.

## Estructura

```
server/
  install.sh                 punto de entrada: menú, argumentos, orquestación
  lib/common.sh              registro, preguntas, comprobaciones, idempotencia
  lib/database.sh            módulo PostgreSQL + TimescaleDB
  lib/mosquitto.sh           módulo broker MQTT (Mosquitto + TLS RSA)
  lib/consumer.sh            módulo consumidor cloud (MQTT a PostgreSQL)
  consumer/                  código del consumidor y su unidad systemd
  db/migrations/001_init.sql esquema base de la telemetría
  db/apply_migrations.sh     runner idempotente de migraciones
  config/modulinkr-server.conf.example
```

## Estado

La base de datos, el broker y el consumidor están desplegados en la VM de Azure (`modulinkr.loaiza.co`). PostgreSQL mantiene las estructuras base, `schema_migrations` y los esquemas físicos por despliegue; Mosquitto sirve TLS en 8883 con el certificado Let's Encrypt y autenticación por usuario y contraseña. El handshake TLS del SIM7028 y la ingestión del consumidor cloud quedaron validados en el despliegue registrado en la bitácora.


## Permiso de borrado de cuarentena, 2026-10-13

El rol del visor conserva el nombre `modulinkr_ro`, sus permisos SELECT y su autenticación existente. La migración `004_quarantine_delete.sql` añade DELETE únicamente sobre public.quarantine. No concede DELETE sobre samples o sample_values ni TRUNCATE. La función db_enable_remote_ro concede también ese permiso cuando provisiona el rol después de las migraciones en una instalación nueva.

El paquete `modulinkr-cuarentena-borrado-vm-20261013.tar.gz` contiene la migración, el runner y `install_quarantine_delete.sh`. El instalador obtiene el nombre de la base desde `/etc/modulinkr/database.env`, comprueba que existen quarantine y modulinkr_ro, aplica la migración mediante el runner y muestra los permisos de borrado sobre cuarentena, muestras y valores. No solicita ni imprime contraseñas y no ejecuta borrado de datos. El visor necesita después su paquete correspondiente y una sesión autenticada para habilitar las acciones. La comprobación del permiso sobre PostgreSQL operativo permanece pendiente.

El instalador asegura también el permiso limitado al finalizar el runner, por lo que una ejecución posterior puede restablecerlo si el rol se volvió a crear. La comprobación final muestra el estado real de las tres tablas; no revoca permisos anteriores ajenos a esta modificación.

## Supervisión de almacenamiento y guardado, 2026-10-13

La migración `005_system_health.sql` y `modulinkr-monitor.service` incorporan la medición del volumen que contiene el directorio de datos de PostgreSQL y el estado del consumidor. El visor consulta esa fila mediante su acceso cifrado existente a PostgreSQL, independiente de MQTT. No se añade otro puerto público ni otra credencial. La medición de la VM y el estado del consumidor caducan a los noventa segundos. Si falla el monitor o la base deja de responder, se conserva la última información disponible con su antigüedad.

`install_supervision.sh` reutiliza `/etc/modulinkr/consumer.env`, aplica únicamente la migración nueva mediante el runner, detecta el volumen a partir del directorio operativo de PostgreSQL e instala las unidades del consumidor y del monitor. El consumidor requiere Paho 2.1 para confirmar mensajes después de procesarlos. El script no modifica las credenciales ni borra muestras. La instalación nueva del componente `consumer` incluye también la supervisión. La comprobación de permisos, capacidad y reentrega en la VM permanece pendiente del despliegue.


## Estado previo a la publicación

Se utiliza exclusivamente el consumidor de identidad física 4.0 y el espacio MQTT `modulinkr/v2`. Los módulos de ingesta del formato interno anterior se retiran del código. Las migraciones numeradas y las tablas de prueba se conservan para reproducir y recuperar el banco; no se ofrecen como histórico de clientes. La definición actual se encuentra en [`db-schema.md`](../shared/protocol/db-schema.md).
