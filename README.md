# ModuLinkr

ModuLinkr es un sistema IoT industrial para adquisición Modbus RTU con transporte LoRa multi-salto y respaldo NB-IoT.

## Estado actual

El banco dispone de un nodo, un supernodo y un gateway. Nodo y supernodo ejecutan el mismo firmware sobre M5Stack Atom Lite; el rol y los periféricos se definen mediante `config.json`. El gateway combina un Heltec WiFi LoRa 32 v3 como radio y un Raspberry Pi Zero 2W para el servicio, el visor y el enlace con la nube.

La última validación de hardware registrada corresponde a `2026-08-02`. Incluye difusión de firmware a dos nodos y cambio coordinado de parámetros de red. Esta fecha describe la evidencia conservada, no el estado instantáneo de los dispositivos.

La arquitectura vigente, los flujos y las fuentes de verdad se describen en [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Componentes

| Ruta | Contenido |
| --- | --- |
| `nodo/` | Firmware común de nodo y supernodo, configuraciones de banco y empaquetado de distribución |
| `gateway/heltec-radio/` | Firmware del Heltec que actúa como radio del gateway |
| `gateway/pi-service/` | Servicio del gateway, buffer SQLite, MQTT y herramientas operativas |
| `gateway/pi-web/` | Visor web local, configuración, diagnóstico y operaciones remotas |
| `server/` | Instalador de PostgreSQL, Mosquitto y consumidor cloud |
| `shared/protocol/` | Especificaciones de trama LoRa, configuración, MQTT y base de datos |

## Capacidades implementadas

El sistema adquiere lecturas Modbus, las transporta por LoRa directo o mediante relay y utiliza NB-IoT cuando no existe ruta al gateway. El gateway conserva un buffer de reenvío, publica al broker cloud y sirve una interfaz local. También están implementados el aprovisionamiento por USB, la configuración por LoRa, la lectura remota de configuración, la actualización individual y difundida de firmware, el diagnóstico Modbus, la salud de radio y el cambio coordinado de parámetros de red.

El canal general de comandos y escrituras Modbus sigue sin implementar. Las secciones correspondientes de `commands-format.md` son normativas para trabajo futuro, no una capacidad disponible.

## Versiones de los formatos

Los formatos han evolucionado con números distintos en la implementación actual:

| Formato | Versión utilizada |
| --- | --- |
| Trama LoRa | `3.9` (`0x39`) |
| Configuración del nodo | `3.3` en las configuraciones de banco; el firmware acepta `3.0` a `3.3` |
| Mensaje MQTT de telemetría | `3.2` |
| Comandos MQTT | Diseño histórico `2.0`, pendiente de implementación |

La relación definitiva entre estos versionados y la compatibilidad entre versiones menores está pendiente de decisión. Hasta resolverla, no se debe inferir compatibilidad solo porque coincida el major.

## Fuentes de verdad

El código y la configuración ejecutable determinan el comportamiento actual. [`shared/protocol/frame-format.md`](shared/protocol/frame-format.md) y el resto de `shared/protocol/` contienen las especificaciones normativas, pero sus marcas de estado deben contrastarse con la implementación cuando exista una discrepancia. La bitácora y los planes situados fuera de este repositorio conservan decisiones y resultados históricos.

## Trabajo pendiente

Los pendientes vigentes se mantienen en `../pendientes.md`, separados entre decisiones de alcance, cambios de código, pruebas de banco y hardware. No se usa como backlog ninguna lista histórica incrustada en la bitácora.

## Flujo de desarrollo

La compilación, carga y monitor serie del firmware se realizan con PlatformIO desde VS Code. Los procedimientos específicos viven en [`nodo/README.md`](nodo/README.md), [`gateway/pi-service/README.md`](gateway/pi-service/README.md), [`gateway/pi-web/README.md`](gateway/pi-web/README.md) y [`server/README.md`](server/README.md).
