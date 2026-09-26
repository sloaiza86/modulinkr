# Despliegue de recorridos observados

La versión del nodo 0.0.62 introduce encaminamiento de custodia mediante relays y comunica los saltos que recorrió cada muestra. La trama LoRa usa schema 3.10 y el mensaje MQTT usa schema 3.3. Se actualizan primero el receptor y el visor del gateway, y después todos los nodos, incluidos los relays y supernodos. Los nodos anteriores no reenvían los nuevos tipos. La transición entre versiones requiere una ventana de mantenimiento; no se garantiza telemetría a través de relays todavía antiguos.

El Heltec comprueba la red y transporta los bytes de la trama al Pi sin interpretar el tipo ni el payload. Esta extensión no requiere modificarlo. El consumidor cloud admite el major 3 y ya almacena `samples.source`; no se modifica PostgreSQL ni se requiere desplegar código en la VM.

## Comportamiento del visor

La topología selecciona los recorridos de la muestra más nueva de cada origen, identificada por su fecha de captura y secuencia. Una muestra nueva sustituye los recorridos de las anteriores, aunque sus plazos todavía no hayan vencido. Una entrega atrasada no recupera una ruta sustituida. Si esa misma muestra se recibe por LoRa y NB-IoT, se muestran las dos vías mientras ambas tengan confirmación vigente. Sin comunicación se conserva únicamente el último recorrido confirmado en gris, sin controles adicionales ni antigüedades en el mapa. Los intermediarios con tráfico de otros orígenes conservan los saltos que ese tráfico confirma.

La línea continua azul representa un salto LoRa hacia el gateway; la amarilla representa LoRa hasta el supernodo; la verde representa la salida NB-IoT. La entrega recibida sin traza mantiene una línea de puntos, sin inventar intermediarios. Los equipos anteriores sin traza conservan la compatibilidad previa con padres anunciados, identificados como tales y sin confirmar un recorrido de muestras.

Los recorridos LoRa caducan a los 135 s y pierden confirmación cuando se desconecta la radio del gateway. Los celulares usan la ventana de muestreo del origen, con el mínimo de 45 s ya establecido, y requieren observación del broker. Republicar una custodia no renueva su fecha. Los beacons conservan su propia fecha de actualización: otra trama o una entrega celular no refrescan un padre antiguo. El estado se consulta cada 2 s y su caducidad y los contadores se proyectan cada segundo.

El nombre del supernodo se resuelve desde el catálogo disponible en el gateway; cuando falta se conserva el identificador. Los intermediarios incluidos en una traza pueden aparecer en el mapa aunque no haya catálogo propio. La gráfica histórica muestra ambas vías sin selector: línea continua para LoRa y punteada para NB-IoT, con un color por medida. Los promedios de cada vía se calculan por separado dentro del intervalo; el detalle conserva sus recuentos y el CSV mantiene la columna `via`. La vía es la primera recepción almacenada de la muestra, no un historial de todos sus reintentos. PostgreSQL no conserva los recorridos; SQLite conserva el último por origen y vía para representar la topología.

## Instalación en el gateway

En el Mac se empaquetan el servicio y el visor. El paquete incluye los componentes del refresco anterior para mantener compatibles el endpoint de resumen y la interfaz.

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/modulinkr-route-observation.tar.gz -C "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/gateway" pi-service/protocol.py pi-service/buffer.py pi-service/mqtt_publisher.py pi-service/gateway_service.py pi-web/netstatus.py pi-web/dataapi.py pi-web/web_service.py pi-web/static/app.js pi-web/static/network-state.js pi-web/static/index.html pi-web/static/components.js pi-web/static/style.css && scp /tmp/modulinkr-route-observation.tar.gz modulinkr@Gateway.local:/tmp/modulinkr-route-observation.tar.gz
```

En la sesión Termius del gateway se detectan las rutas instaladas y se guarda una copia previa. Se comprueba la sintaxis antes de detener los servicios. El bloque reinicia los servicios también si falla una copia; ante un error se revisa la copia previa indicada antes de continuar con los dispositivos.

```bash
bash <<'SH'
set -euo pipefail
route_gw=$(systemctl show modulinkr-gateway.service -p WorkingDirectory --value)
route_web=$(systemctl show modulinkr-web.service -p WorkingDirectory --value)
test -n "$route_gw" && test -f "$route_gw/gateway_service.py"
test -n "$route_web" && test -f "$route_web/web_service.py"
route_stage=$(mktemp -d /tmp/modulinkr-route-observation.XXXXXX)
tar -xzf /tmp/modulinkr-route-observation.tar.gz -C "$route_stage"
python3 -m compileall -q "$route_stage/pi-service" "$route_stage/pi-web"
mkdir -p "$route_stage/backup/pi-service" "$route_stage/backup/pi-web/static"
for route_file in protocol.py buffer.py mqtt_publisher.py gateway_service.py; do
  sudo cp -a "$route_gw/$route_file" "$route_stage/backup/pi-service/$route_file"
done
for route_file in netstatus.py dataapi.py web_service.py static/app.js static/network-state.js static/index.html static/components.js static/style.css; do
  if sudo test -f "$route_web/$route_file"; then
    sudo cp -a "$route_web/$route_file" "$route_stage/backup/pi-web/$route_file"
  fi
done
printf 'Copia previa: %s/backup\n' "$route_stage"
trap 'sudo systemctl start modulinkr-gateway.service modulinkr-web.service' EXIT
sudo systemctl stop modulinkr-web.service modulinkr-gateway.service
for route_file in protocol.py buffer.py mqtt_publisher.py gateway_service.py; do
  sudo install -m 644 "$route_stage/pi-service/$route_file" "$route_gw/$route_file"
  sudo cmp "$route_stage/pi-service/$route_file" "$route_gw/$route_file"
done
for route_file in netstatus.py dataapi.py web_service.py static/app.js static/network-state.js static/index.html static/components.js static/style.css; do
  sudo install -m 644 "$route_stage/pi-web/$route_file" "$route_web/$route_file"
  sudo cmp "$route_stage/pi-web/$route_file" "$route_web/$route_file"
done
sudo systemctl start modulinkr-gateway.service modulinkr-web.service
trap - EXIT
systemctl is-active modulinkr-gateway.service modulinkr-web.service
sudo journalctl -u modulinkr-gateway.service -u modulinkr-web.service -n 60 --no-pager
SH
```

Se recarga el navegador tras la instalación. La creación de `delivery_routes` y de la fecha del padre se realiza al abrir el buffer con el servicio nuevo. No se vacían muestras ni catálogos.

## Dispositivos y banco

En el Mac, desde el proyecto `firmware/nodo` abierto en VS Code, se ejecuta `Cmd+Shift+P`, `PlatformIO: Build`. Tras una compilación correcta se usa `PlatformIO: Upload` para cada nodo, relay y supernodo conservando la configuración que corresponde a cada dispositivo. Los binarios de distribución existentes no se han regenerado. Si se distribuye la imagen compilada, después del Build se empaqueta en el Mac:

```bash
cd "/Users/santiago/Documents/Documentos Academicos/Master/TFM/firmware/nodo" && ./make_dist.sh
```

La prueba del recorrido con un relay intermedio requiere al menos tres dispositivos: origen, relay y supernodo. Con dos se verifica únicamente la custodia directa. El monitor serie se abre para un dispositivo a la vez mediante `PlatformIO: Monitor`.

Se comprueba primero la entrega LoRa al gateway y que el mapa coincida con los saltos recibidos. Después se desconecta la radio del gateway y se verifica la búsqueda, oferta inversa, custodia y recepción celular con los mismos identificadores de muestra. Se corta el relay o la salida celular para comprobar caducidad y nueva búsqueda; se recupera LoRa y se verifica la entrega normal. Una muestra retenida y republicada debe conservar su fecha de recorrido. Se comprueba también el reinicio del supernodo con custodia pendiente, el nombre mostrado, los filtros, los recuentos mixtos y el CSV contra `samples.source`.

## Archivos de la implementación

Dentro de `firmware/` se modifican `nodo/src/main.cpp`, `nodo/src/protocol.h`, `nodo/src/lora.h`, `nodo/src/lora.cpp`, `nodo/src/route_trace.h`, `nodo/src/outbox.h`, `nodo/src/outbox.cpp`, `nodo/src/pending.h`, `nodo/src/pending.cpp` y `nodo/tests/test_route_trace.py`.

En el gateway se modifican `gateway/pi-service/protocol.py`, `gateway/pi-service/buffer.py`, `gateway/pi-service/mqtt_publisher.py`, `gateway/pi-service/gateway_service.py`, `gateway/pi-service/tests/test_route_trace.py`, `gateway/pi-web/netstatus.py`, `gateway/pi-web/dataapi.py`, `gateway/pi-web/static/app.js`, `gateway/pi-web/static/network-state.js`, `gateway/pi-web/static/index.html`, `gateway/pi-web/tests/test_delivery_routes.py`, `gateway/pi-web/tests/test_delivery_routes_ui.cjs` y `gateway/pi-web/tests/test_data_sources.py`.

La documentación versionada comprende `shared/protocol/frame-format.md`, `shared/protocol/batch-format.md` y este documento. Fuera del repositorio se añade la entrada correspondiente a `TFM/bitacora.md`; ese archivo no requiere commit.

## Validación local y pendiente

Pasan 121 comprobaciones locales: 25 del servicio, 19 de estado y topología, tres de datos, nueve de disponibilidad LoRa, tres del nodo y 62 de interfaz. Las pruebas C++ ejecutan el código de codificación, bandeja y encaminamiento con dependencias simuladas. La consulta de datos usa una adaptación de sintaxis a SQLite para comprobar resultados; no constituye una prueba contra PostgreSQL. No se ha compilado el firmware completo para ESP32, desplegado servicios ni probado el hardware. La inspección visual de esta versión sigue pendiente porque no se pudo autorizar el servidor local de prueba por un límite de uso del entorno.
