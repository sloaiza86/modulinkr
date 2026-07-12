# ModuLinkr, control de acceso al medio y análisis de tráfico LoRa

Documento de análisis del comportamiento del canal LoRa compartido: cuánto
tráfico redundante genera la red, por qué, y qué mecanismos de control de
acceso al medio (MAC) se aplican para que escale. Complementa a
`frame-format.md` (formato de trama y reglas de enrutamiento) con la capa de
acceso al canal.

Iniciado el 6 de julio de 2026. Es un documento vivo: el análisis se abre
con lo observado en banco y se va completando con mediciones a medida que se
aplican las mejoras.

## 1. El problema: half-duplex y canal compartido

LoRa es **half-duplex**: mientras un dispositivo transmite, no escucha. Y
todos los dispositivos del despliegue comparten la **misma frecuencia y el
mismo canal** (869.525 MHz, SF7, BW125 en la configuración de referencia).
Dos consecuencias:

1. **Colisiones**: si dos nodos transmiten a la vez, sus señales se solapan y
   ninguna trama llega. El emisor no se entera (no escuchaba), así que lo
   detecta por ausencia de ACK y retransmite, lo que genera más tráfico y
   más probabilidad de nuevas colisiones.
2. **Contención**: el tiempo de aire es un recurso finito. Cada trama ocupa
   el canal durante su Time-on-Air (~57 ms para una TELEMETRY de 2 lecturas a
   SF7). Cuantas más tramas por segundo, más se acerca el canal a la
   saturación.

Con pocos nodos y cadencia baja (el banco: 1 nodo + 1 supernodo cada 5 s),
las colisiones son raras y el problema no se manifiesta. Pero el diseño debe
sostener más nodos, relays y retransmisiones simultáneas sin colapsar.

## 2. Tráfico redundante por muestra útil (observado en banco, 6-jul-2026)

Medido sobre el log del banco con el nodo 2 emitiendo hacia su padre (nodo
1), con enlace directo marginal al gateway (RSSI -96 a -105 dBm) y relay
sano por el nodo 1 (RSSI -50 dBm). **Cada muestra útil** generaba en el aire:

1. Emisión del nodo 2 hacia su padre.
2. Relay del nodo 1 hacia el gateway.
3. ACK del gateway a la trama que llegó por el relay.
4. **ACK del gateway a la trama que oyó directa del nodo 2**, que en realidad
   no iba dirigida a él (ver §3, causa A).
5. Relay del ACK de vuelta del nodo 1 al nodo 2.

Más las retransmisiones cuando el ACK de vuelta no llegaba por el enlace
marginal. En números redondos, del orden de 5 transmisiones por muestra útil
en régimen bueno, subiendo a 8-10 con retransmisiones. No escala.

## 3. Causas del tráfico redundante, por impacto

### Causa A: el gateway confirmaba tramas que no le iban dirigidas (regresión)

Al migrar la generación del ACK del Heltec al Pi (6-jul-2026), el servicio
del Pi comprobaba `dest_id == gateway` (destino final) pero **no**
`hop_dst == gateway` (destino de este salto). El firmware autónomo anterior
del Heltec sí filtraba por `hop_dst`. La consecuencia: cuando el nodo 2
transmitía hacia su padre (nodo 1), esa trama llevaba `hop_dst=1` pero
`dest_id=gateway`; el gateway la oía de refilón por proximidad y, al mirar
solo `dest_id`, la confirmaba, **duplicando el ACK**. Es la fuente del ACK
del camino directo marginal.

**Corrección (6-jul-2026)**: el gateway procesa solo tramas con
`hop_dst == gateway` (regla de `frame-format.md` §10.6). Coste: una
condición. Impacto esperado: eliminar el ACK del camino directo, ~50 % menos
ACK. Las tramas oídas de refilón se contabilizan aparte (`overheard`) para
el análisis.

### Causa B: half-duplex sin escucha previa (sin CSMA/LBT)

Los nodos transmiten cuando les toca (cadencia + reintentos + relays + ecos
de beacon) **sin comprobar si el canal está libre**. Con pocos nodos y el
jitter aplicado a beacons y ofertas, las colisiones son raras; al escalar se
disparan. La solución estándar es *Listen Before Talk* (LBT) / CSMA/CA.

### Causa C: retransmisiones agresivas con enlace marginal

Un enlace pobre pierde ACKs, lo que dispara retransmisiones a intervalo
fijo, que suman tráfico y colisiones. Un backoff con jitter suaviza el
efecto de tormenta.

### Causa D: doble camino inherente a la malla

El nodo llega al gateway por dos rutas simultáneas (directa marginal +
relay). Da fiabilidad, cuesta tráfico. Se afina con la calibración de la
selección de padre, no se elimina sin perder robustez.

## 4. Mecanismos de mejora

### 4.1 Filtro de salto en el gateway (aplicado 6-jul-2026)

Ver Causa A. El gateway solo confirma el salto que le va dirigido.

### 4.2 Supresión de ACK duplicado por ventana temporal (aplicado 6-jul-2026)

Si la misma `(origin, seq)` llega dos veces en una ventana corta (< 1 s), es
multi-camino (no un reintento) y se confirma una sola vez. Un reintento real
llega tras el timeout (segundos después) y sí se confirma.

**Implementación (gateway)**: `gateway_service.py` guarda en `recent_acks` el
instante del último ACK por `(origin, seq)`. Antes de confirmar, si esa clave
se confirmó hace menos de `ack_window_s` (variable de entorno
`MODULINKR_ACK_WINDOW_S`, 1.0 s por defecto), se cuenta como `acksup` y no se
reemite el ACK. El dato ya quedó en el buffer (la deduplicación de custodia es
independiente). La tabla `recent_acks` se poda en cada `STATS`. La línea
`STATS` añade el contador `acksup`.

Nota: es complementaria al filtro de salto §4.1. El filtro elimina el ACK del
camino directo cuando el gateway oye la trama del nodo dirigida a su padre; la
supresión por ventana cubre el caso en que la misma muestra llega de verdad
dos veces dirigida al gateway (p. ej. directo al gateway + relay), que el
filtro de salto no descarta porque ambas van con `hop_dst=GW`.

### 4.3 Listen Before Talk con CAD (investigado, viable con limitaciones)

El SX1262 tiene *Channel Activity Detection* (CAD) en hardware: escucha muy
brevemente y detecta si hay una señal LoRa presente, con bajo consumo. Con
CAD se implementa CSMA/CA: antes de transmitir, el nodo hace CAD; si el
canal está ocupado, espera un backoff aleatorio y reintenta.

Ventaja regulatoria: en la sub-banda g3 de EU868, **con LBT el duty cycle
legal sube del 1 % al 10 %**.

**Resultado de la investigación (6-jul-2026).** Los nodos no hablan con el
SX1262 directamente, sino con el STM32WLE5 del Atom DTU (módulo RAK3172) vía
firmware AT RUI3. Verificado en la documentación oficial de RAKwireless y en
los hilos de su foro (respuestas de Bernd Giesecke, RAK):

- **Sí existe CAD en P2P por AT**: comando `AT+CAD` (`AT+CAD=1` activa,
  `AT+CAD=0` desactiva), añadido en el firmware RUI3 **V4.0.6** (oct-2023).
  El ajuste se **guarda en flash** (persiste entre arranques). El SX1262
  soporta CAD en hardware, por eso RAK eligió CAD y no LBT-por-RSSI para P2P.
- **Cómo opera**: con `AT+CAD=1`, cada `AT+PSEND` hace CAD en la frecuencia
  de envío antes de transmitir. Si el canal está libre, transmite y devuelve
  `+EVT:TXP2P DONE`; si detecta actividad, no transmite. Coste medido por un
  usuario: **~32 ms** de escucha añadidos por trama cuando el canal está
  libre.
- **`AT+LBT` no nos sirve**: existe (`AT+LBT`, `AT+LBTRSSI`, `AT+LBTSCANTIME`)
  pero es **solo para LoRaWAN y solo regiones Corea/Japón**, no para P2P ni
  EU868. Para nuestro caso (P2P, EU868) el mecanismo aplicable es `AT+CAD`.

**Limitación importante (a resolver en el firmware del nodo).** La
implementación RUI3 de CAD es tosca: **no expone CAD independiente ni timeout
configurable**. RAK confirma (sep-2025) que "no se puede lanzar CAD y leer el
resultado por separado: va acoplado a una transmisión". Cuando el canal está
ocupado, el comportamiento reportado es inconsistente: o reintenta CAD de
forma indefinida (bloqueante, se observaron hasta 3 minutos, inviable con
batería) o devuelve `AT_BUSY_ERROR` y hay que reemitir el `AT+PSEND`. No hay
forma de fijar un backoff máximo desde el firmware AT.

**Cómo lo integramos.** Esa limitación encaja con el backoff propio del nodo
(§4.4): usamos `AT+CAD=1` para obtener la señal de "canal ocupado"
(`AT_BUSY_ERROR`), y es **nuestro firmware** quien aporta la lógica de backoff
con jitter que RUI3 no da, en vez de dejar que el módulo bloquee. Es decir,
CAD nos da la detección; nosotros ponemos la política de reintento.

**Confirmado en banco (6-jul-2026)**: el nodo 2 (Atom DTU EU868) reporta
`AT+VER` = **`RUI_4.0.6_RAK3172-E`**, justo la versión en la que se añadió
`AT+CAD`. Es decir, el CAD está disponible en el hardware en mano; no hace
falta actualizar el módulo. Falta confirmar la versión del segundo DTU (misma
remesa, se asume igual). El gateway (Heltec, RadioLib sobre SX1262 puro) ya
tiene CAD directo, pero transmite poco (solo ACK y beacon); el grueso del
tráfico lo generan los nodos, así que el CAD relevante es el suyo.

La consulta de versión quedó permanente en el firmware del nodo: `begin()`
pregunta `AT+VER=?` y la muestra en el banner de arranque (`LoraP2P::
queryVersion()`), para tener a la vista la capacidad CAD de cada unidad.

**Implementación (nodo, aplicado 6-jul-2026).** `LoraP2P::begin()` fija
`AT+CAD=1` (vía `module_.sendCommand`, tras `config` y antes del modo TX+RX);
el resultado se guarda en `cad_ok_` y se muestra en el banner (`CAD: on/off`).
A partir de ahí, cada `AT+PSEND` hace CAD antes de transmitir.

Manejo del canal ocupado (decisión: resolverlo ya, no en la
Fase 3). Cuando el CAD reporta `AT_BUSY_ERROR` la trama NO salió al aire; el
driver programa un **reintento rápido** de la última trama tras un backoff
corto (60 ms base + 0-60 ms de jitter), hasta `kBusyMaxTries` (3). Es
independiente del backoff de ACK de §4.4, que cubre el ACK perdido *después*
de transmitir: aquí la trama nunca salió. Si se agotan los reintentos rápidos,
la recupera el backoff de ACK. Un envío nuevo cancela un reintento rápido
pendiente de una trama anterior (la vieja, si no salió, la cubre el backoff de
ACK). Contador `busyEvents()` (`cad_busy=` en el log de tx) como indicador de
contención del medio.

Nota sobre la limitación de RUI3 (§ arriba): en V4.0.6 el CAD puede, en vez de
devolver `AT_BUSY_ERROR`, **bloquear** el `AT+PSEND` reintentando hasta que el
canal quede libre. En el banco actual (tráfico bajo, canal casi siempre libre)
esto no se manifiesta; se observará bajo contención en la prueba de estrés
(§5), que es donde se decidirá si hace falta mitigar el bloqueo.

### 4.4 Backoff exponencial con jitter en retransmisiones (aplicado 6-jul-2026)

En vez de reintentar a intervalo fijo, el tiempo de espera del ACK crece con
cada reintento, con techo, más un jitter aleatorio. Reduce el efecto de
tormenta cuando varios nodos reintentan a la vez y los desincroniza.

**Regla (simple)**: el primer envío espera `ack_timeout` (3 s). Cada reintento
duplica ese intervalo base con techo de 12 s, y le suma un jitter aleatorio de
0 a 500 ms. Con `max_retries=2` la secuencia de esperas queda 3 s → ~6 s →
~12 s (peor caso ~21 s antes de caer a outbox / NB-IoT). El techo no recorta
con 2 reintentos; es red de seguridad si `max_retries` sube.

**Implementación (nodo)**: `backoffTimeoutMs(retries)` en `main.cpp` calcula
el intervalo; la cola `PendingQueue` guarda un `timeout_ms` por entrada
(0 = usar el base en el primer intento) que `firstExpired` respeta. El jitter
usa `esp_random()` (RNG por hardware). Aplica a las dos rutas de reintento
(gateway y custodia a supernodo). El log de `retx` imprime `wait=<ms>` para
observar el backoff y el jitter en banco.

**Relación con CAD (§4.3)**: este backoff es también la política de reintento
que RUI3 no da cuando `AT+CAD` reporta canal ocupado (`AT_BUSY_ERROR`).

### 4.5 Calibración de parent_min_rssi (previsto)

Ajustar el umbral de elegibilidad de padre para que un nodo prefiera un
vecino con enlace fuerte a un salto de más, en vez de un enlace directo
marginal al gateway. Reduce el doble camino en origen.

## 5. Estrategia de validación con pocos nodos

Con 1 nodo + 1 supernodo casi no hay colisiones, así que la validación se
separa según lo que cada mejora produce:

- **Reducción de tráfico** (filtro de salto §4.1, supresión de ACK §4.2): se
  mide **contando** (contadores del servicio del Pi: `rx`, `ack`, `dup`,
  `overheard`). Determinista, 2 nodos bastan.
- **Comportamiento** (selección de padre §4.5, backoff §4.4): se valida
  **observando** qué padre elige el nodo y cómo espacia los reintentos. 2
  nodos bastan.
- **Colisiones y LBT** (§4.3): necesita **provocar contención**
  artificialmente. Prueba de estrés: bajar la cadencia del nodo (de 5 s a
  1 s o menos) y añadir un tercer emisor (Atom Lite + DTU EU868 libres) para
  inducir colisiones reales y medir el efecto del LBT.

## 6. Mediciones

Instrumentación: el servicio del Pi emite una línea `STATS rx=... ack=...
acksup=... dup=... beacon=... overheard=... notconf=... drop=... buffer=...`
cada `MODULINKR_STATS_S` segundos (60 por defecto). La ratio `ack / rx`, el
conteo `overheard` y ahora `acksup` (ACK suprimidos por multi-camino) son los
indicadores clave del tráfico redundante.

### 6.1 Antes del filtro de salto (baseline)

El build previo a la corrección no tenía la instrumentación `STATS`, así que
el baseline se toma del log del banco: por cada muestra útil del nodo 2
aparecían **dos** líneas `ack`, una por la trama que llegaba por el relay
(`hop_dst=GW`) y otra por la que el gateway oía de refilón directa del nodo 2
(`hop_dst=1`, no dirigida a él). Es decir, **2 ACK por muestra útil**.

### 6.2 Después del filtro de salto (§4.1)

Capturado el 6-jul-2026, 1 nodo + 1 supernodo, cadencia 5 s. Ventana estable
de 60 s (STATS 15:02:26 a 15:03:26):

| Contador | t0 | t0+60 s | delta |
| --- | --- | --- | --- |
| rx | 24 | 64 | +40 |
| ack | 7 | 19 | +12 |
| dup | 7 | 19 | +12 |
| overheard | 17 | 45 | +28 |
| notconf | 0 | 0 | 0 |
| drop | 0 | 0 | 0 |

Resultado: **1 ACK por muestra útil** (antes 2), ratio `ack/rx` = 12/40 =
0.30. El descuadre cuadra: de las 40 tramas parseadas, 28 son `overheard`
(camino directo del nodo 2, ecos del propio ACK del gateway y ecos de beacon)
y 12 son las confirmadas por el relay. Las tramas del camino directo marginal,
antes confirmadas con un ACK redundante, ahora solo se cuentan como
`overheard`. **La corrección de la Causa A elimina ~50 % de los ACK.**

Nota de medición: en esta corrida todo salió marcado `[dup]` y `buffer` se
mantuvo en 96 porque la BBDD del Pi persiste en disco y el nodo reinició su
`seq` desde 1, colisionando con entradas de una corrida anterior. La
deduplicación funciona; para un baseline limpio de `buffer` conviene vaciar
la BBDD antes de medir. No afecta al conteo de ACK (se emite un ACK por trama
recibida, nueva o duplicada).

### 6.3 Fase 1: supresión de ACK (§4.2) y backoff (§4.4)

Capturado el 6-jul-2026, misma topología (nodo 2 con padre nodo 1, relay al
gateway). STATS de referencia: `rx=30 ack=9 acksup=0 dup=9 overheard=21
notconf=0 drop=0`. Ratio `ack/rx` = 0.30, igual que en §6.2: sin regresión.

`acksup=0` es **el resultado esperado en esta topología**: la trama directa
del nodo 2 va con `hop_dst=1` y el filtro de salto (§4.1) ya la descarta como
`overheard` antes de llegar a la ventana de supresión; al gateway solo llega
una copia (la del relay, `hop_dst=GW`). La supresión por ventana solo actúa
cuando la misma muestra llega dos veces dirigida al gateway (nodo con el
gateway como padre y además una copia por relay), caso que no se da aquí. Está
cableada y verificada como camino, pendiente de disparar en una topología que
lo provoque o en la prueba de estrés (§5).

El backoff (§4.4) no es observable desde el gateway: los seq repetidos que
llegan (p. ej. seq=3 y seq=10 dos veces, separados varios segundos) confirman
que el nodo retransmite al perder el ACK de vuelta, pero el espaciado exacto
(`wait=` con jitter) solo se ve en el monitor del nodo. Validación del backoff:
pendiente de captura en el lado del nodo.

## 7. Decisiones y estado

| Mecanismo | Estado | Fecha |
| --- | --- | --- |
| Filtro de salto en el gateway (§4.1) | aplicado y medido (~50 % menos ACK) | 6-jul-2026 |
| Instrumentación de tráfico (§6) | aplicada | 6-jul-2026 |
| Supresión de ACK duplicado (§4.2) | aplicado, pendiente de medir | 6-jul-2026 |
| LBT con CAD (§4.3) | aplicado (nodo): AT+CAD=1 + reintento rápido ante busy; pendiente de estrés | 6-jul-2026 |
| Backoff con jitter (§4.4) | aplicado (nodo), pendiente de medir | 6-jul-2026 |
| Calibración parent_min_rssi (§4.5) | previsto | |
| Prueba de estrés y análisis de escalabilidad (§5) | previsto | |

## 8. Documentos relacionados

- [`frame-format.md`](frame-format.md): formato de trama, enrutamiento mesh,
  reglas de validación (incluida §10.6, el filtro de salto).
- [`node-config.md`](node-config.md): parámetros de red y del bloque `mesh`.
