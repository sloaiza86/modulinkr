# ModuLinkr, arquitectura vigente

Este documento describe los componentes y flujos implementados. La historia de los pivotes arquitectónicos se conserva en `../Red V1.md` a `../Red V4.md` y en `../bitacora.md`.

## 1. Objetivo

ModuLinkr adquiere medidas de dispositivos Modbus RTU y mantiene su entrega cuando se degrada la red local o el gateway deja de estar disponible. LoRa forma la red de campo y NB-IoT proporciona la salida celular de contingencia.

El alcance implementado se concentra en adquisición, transporte, observabilidad, configuración y mantenimiento remoto. El canal general de control Modbus permanece pendiente.

## 2. Componentes

### 2.1 Nodo

El nodo utiliza un M5Stack Atom Lite con Atom DTU LoRaWAN EU868 y un dispositivo Modbus. Lee el bus RS-485, serializa la telemetría y participa en la red LoRa. Puede actuar como relay cuando su configuración lo permite.

### 2.2 Supernodo

El supernodo utiliza el mismo firmware y plataforma base, con una NB-IoT 2 Unit SIM7028. Además de medir y retransmitir LoRa, puede custodiar muestras de otros nodos y publicarlas al broker cuando el gateway no está disponible.

### 2.3 Gateway

El gateway separa radio y lógica. El Heltec WiFi LoRa 32 v3 recibe y transmite tramas, mientras que el Raspberry Pi Zero 2W valida el protocolo, mantiene el árbol de rutas, genera ACK y BEACON, conserva el buffer SQLite y publica al broker cloud.

El visor web corre en el mismo Pi. Muestra estado, topología y datos, y ofrece configuración, diagnóstico, aprovisionamiento y operaciones de firmware.

### 2.4 Nube

Mosquitto recibe la telemetría del gateway y de los supernodos. El consumidor Python procesa catálogos, telemetría y salud, y los persiste en PostgreSQL. La base cloud es la fuente de verdad del histórico. El SQLite del gateway es un buffer operativo y una fuente de estado local, no una segunda base histórica.

## 3. Flujo normal

El nodo obtiene hora, se registra y anuncia su catálogo. Cada ciclo válido produce una TELEMETRY con timestamp, valores y estado Modbus. La trama sube por el padre elegido hasta el gateway. El Pi la acepta en su buffer antes de emitir el ACK, de modo que la confirmación significa custodia efectiva y no solo recepción de radio.

El publicador MQTT drena el buffer con QoS 1. El consumidor cloud deduplica por `(origin, ts, seq)` y asocia los valores con el catálogo vigente del nodo.

## 4. Caída del gateway

Sin BEACON ni ACK, los nodos pierden la ruta raíz y conservan las muestras en la outbox. Un nodo puede elegir un supernodo mediante SN_REQUEST y SN_OFFER. El supernodo confirma la custodia y publica las muestras por NB-IoT. Cuando reaparece el gateway, la ruta LoRa normal se recupera automáticamente.

La entrega del catálogo de un nodo normal a través del supernodo está especificada como register en custodia, pero no está implementada en el firmware. Por ello, el alta zero-touch de un nodo que nunca alcanzó al gateway conserva ese hueco.

## 5. Configuración y mantenimiento

El `config.json` vive en LittleFS y define identidad, radio, mesh, NB-IoT y dispositivos Modbus. Puede cargarse por USB o transferirse por LoRa. Una configuración nueva queda a prueba y se revierte si el nodo no vuelve a registrarse.

El firmware puede entregarse individualmente o por difusión. Ambos caminos comparten instalación, verificación de arranque y reversión. El transporte secuencial anterior sigue presente y está pendiente de retirada después de validar de forma deliberada la reversión del gestor de arranque.

El cambio de frecuencia, ancho de banda, Network ID, TTL o clave requiere coordinación. El gateway distribuye a cada nodo una modificación de su propia configuración y cambia su radio cuando terminan las confirmaciones.

## 6. Seguridad

La interfaz aire admite AES-CCM extremo a extremo entre los nodos y el Pi. El Heltec transporta bytes opacos y no conoce la clave. MQTT utiliza autenticación y TLS; el SIM7028 requiere un certificado de servidor RSA según la validación de banco registrada.

Los secretos se solicitan durante la instalación y permanecen fuera del repositorio. Las configuraciones versionadas contienen únicamente valores no sensibles de banco.

## 7. Versionado y compatibilidad

La implementación usa `0x39` en la trama LoRa, configuraciones de nodo `3.3` y mensajes MQTT `3.2`. Algunas extensiones posteriores aparecen descritas como v4.0 y v4.1, aunque el byte de trama permanece en `0x39`.

También existen cambios de layout entre versiones menores del major 3, mientras que los receptores aceptan cualquier minor con ese major. La política definitiva está pendiente: puede separarse el versionado por formato o alinearse con una única secuencia, pero no se adopta ninguna de las dos opciones en este documento.

## 8. Estado de validación

La evidencia de banco registrada hasta `2026-08-02` cubre adquisición Modbus, mesh, respaldo NB-IoT, seguridad, consumidor cloud, visor, configuración remota, actualización individual, difusión a dos nodos, vigilancia de radio y cambio coordinado de red.

Siguen pendientes pruebas deliberadas de la ventana de silencio, reversión del gestor de arranque, duty cycle al 8 %, guarda del receptor y contención MAC. El detalle vigente se mantiene en `../pendientes.md`.

## 9. Fuentes técnicas

La trama LoRa se define en [`shared/protocol/frame-format.md`](shared/protocol/frame-format.md), la configuración en [`shared/protocol/node-config.md`](shared/protocol/node-config.md), los mensajes MQTT en [`shared/protocol/batch-format.md`](shared/protocol/batch-format.md) y la persistencia en [`shared/protocol/db-schema.md`](shared/protocol/db-schema.md). La implementación prevalece cuando se detecta una discrepancia sin resolver.
