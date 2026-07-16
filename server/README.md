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

La base de datos y el broker están desplegados y corriendo en la VM de Azure (`modulinkr.loaiza.co`): PostgreSQL con las cinco tablas del esquema más `schema_migrations`, y Mosquitto sirviendo TLS en 8883 con el certificado Let's Encrypt y autenticación por usuario y contraseña. Pendiente de validar en hardware el handshake TLS del SIM7028 contra el broker. El consumidor cloud (fase 3) está implementado como componente `consumer` del instalador (16-jul-2026), pendiente de desplegar y validar en la VM.
