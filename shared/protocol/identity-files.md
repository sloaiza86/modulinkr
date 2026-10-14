# Archivos de la protección de identidad

Se enumera el alcance original de la implementación de identidad física: firmware del nodo, gateway, visor, consumidor, protocolo, instaladores y pruebas. Los binarios de distribución no se han regenerado. La configuración local de asistentes queda fuera de este alcance. El barrido posterior y su inventario se recogen en [`prepublic-audit.md`](prepublic-audit.md).

## Nodo

- [`nodo/README.md`](../../nodo/README.md)
- [`nodo/src/commission.cpp`](../../nodo/src/commission.cpp)
- [`nodo/src/config.cpp`](../../nodo/src/config.cpp)
- [`nodo/src/config.h`](../../nodo/src/config.h)
- [`nodo/src/device_identity.cpp`](../../nodo/src/device_identity.cpp)
- [`nodo/src/device_identity.h`](../../nodo/src/device_identity.h)
- [`nodo/src/fwbcast.cpp`](../../nodo/src/fwbcast.cpp)
- [`nodo/src/fwbcast.h`](../../nodo/src/fwbcast.h)
- [`nodo/src/lora.cpp`](../../nodo/src/lora.cpp)
- [`nodo/src/lora.h`](../../nodo/src/lora.h)
- [`nodo/src/main.cpp`](../../nodo/src/main.cpp)
- [`nodo/src/nbiot_service.cpp`](../../nodo/src/nbiot_service.cpp)
- [`nodo/src/nbiot_service.h`](../../nodo/src/nbiot_service.h)
- [`nodo/src/outbox.cpp`](../../nodo/src/outbox.cpp)
- [`nodo/src/outbox.h`](../../nodo/src/outbox.h)
- [`nodo/src/pending.cpp`](../../nodo/src/pending.cpp)
- [`nodo/src/pending.h`](../../nodo/src/pending.h)
- [`nodo/src/protocol.h`](../../nodo/src/protocol.h)
- [`nodo/tests/identity_fixture.py`](../../nodo/tests/identity_fixture.py)
- [`nodo/tests/test_device_identity.py`](../../nodo/tests/test_device_identity.py)
- [`nodo/tests/test_firmware_transport.py`](../../nodo/tests/test_firmware_transport.py)
- [`nodo/tests/test_mqtt_batch_size.py`](../../nodo/tests/test_mqtt_batch_size.py)
- [`nodo/tests/test_mqtt_status.py`](../../nodo/tests/test_mqtt_status.py)
- [`nodo/tests/test_register_fragmentation.py`](../../nodo/tests/test_register_fragmentation.py)
- [`nodo/tests/test_route_trace.py`](../../nodo/tests/test_route_trace.py)
- [`nodo/tests/test_write_catalog.py`](../../nodo/tests/test_write_catalog.py)
- [`nodo/tests/test_write_routing.py`](../../nodo/tests/test_write_routing.py)

## Servicio del gateway

- [`gateway/pi-service/README.md`](../../gateway/pi-service/README.md)
- [`gateway/pi-service/buffer.py`](../../gateway/pi-service/buffer.py)
- [`gateway/pi-service/firmware_images.py`](../../gateway/pi-service/firmware_images.py)
- [`gateway/pi-service/flash_nodo.sh`](../../gateway/pi-service/flash_nodo.sh)
- [`gateway/pi-service/gateway_service.py`](../../gateway/pi-service/gateway_service.py)
- [`gateway/pi-service/identity.py`](../../gateway/pi-service/identity.py)
- [`gateway/pi-service/install_identity.sh`](../../gateway/pi-service/install_identity.sh)
- [`gateway/pi-service/modbus_command.py`](../../gateway/pi-service/modbus_command.py)
- [`gateway/pi-service/mqtt_publisher.py`](../../gateway/pi-service/mqtt_publisher.py)
- [`gateway/pi-service/node_commands.py`](../../gateway/pi-service/node_commands.py)
- [`gateway/pi-service/node_maintenance.py`](../../gateway/pi-service/node_maintenance.py)
- [`gateway/pi-service/protocol.py`](../../gateway/pi-service/protocol.py)
- [`gateway/pi-service/tests/test_firmware_install_check.py`](../../gateway/pi-service/tests/test_firmware_install_check.py)
- [`gateway/pi-service/tests/test_firmware_nvs.py`](../../gateway/pi-service/tests/test_firmware_nvs.py)
- [`gateway/pi-service/tests/test_modbus_commands.py`](../../gateway/pi-service/tests/test_modbus_commands.py)
- [`gateway/pi-service/tests/test_nbiot_observation.py`](../../gateway/pi-service/tests/test_nbiot_observation.py)
- [`gateway/pi-service/tests/test_physical_identity.py`](../../gateway/pi-service/tests/test_physical_identity.py)

## Visor

- [`gateway/pi-web/README.md`](../../gateway/pi-web/README.md)
- [`gateway/pi-web/commandapi.py`](../../gateway/pi-web/commandapi.py)
- [`gateway/pi-web/configapi.py`](../../gateway/pi-web/configapi.py)
- [`gateway/pi-web/dataapi.py`](../../gateway/pi-web/dataapi.py)
- [`gateway/pi-web/firmwaremeta.py`](../../gateway/pi-web/firmwaremeta.py)
- [`gateway/pi-web/identityapi.py`](../../gateway/pi-web/identityapi.py)
- [`gateway/pi-web/maintenanceapi.py`](../../gateway/pi-web/maintenanceapi.py)
- [`gateway/pi-web/mqttapi.py`](../../gateway/pi-web/mqttapi.py)
- [`gateway/pi-web/netstatus.py`](../../gateway/pi-web/netstatus.py)
- [`gateway/pi-web/otaapi.py`](../../gateway/pi-web/otaapi.py)
- [`gateway/pi-web/static/app.js`](../../gateway/pi-web/static/app.js)
- [`gateway/pi-web/static/identity.js`](../../gateway/pi-web/static/identity.js)
- [`gateway/pi-web/static/index.html`](../../gateway/pi-web/static/index.html)
- [`gateway/pi-web/static/quarantine.js`](../../gateway/pi-web/static/quarantine.js)
- [`gateway/pi-web/static/style.css`](../../gateway/pi-web/static/style.css)
- [`gateway/pi-web/supervision.py`](../../gateway/pi-web/supervision.py)
- [`gateway/pi-web/systemapi.py`](../../gateway/pi-web/systemapi.py)
- [`gateway/pi-web/tests/test_command_api.py`](../../gateway/pi-web/tests/test_command_api.py)
- [`gateway/pi-web/tests/test_firmware_flash_guards.py`](../../gateway/pi-web/tests/test_firmware_flash_guards.py)
- [`gateway/pi-web/tests/test_firmware_identity.py`](../../gateway/pi-web/tests/test_firmware_identity.py)
- [`gateway/pi-web/tests/test_firmware_usb_lifecycle.cjs`](../../gateway/pi-web/tests/test_firmware_usb_lifecycle.cjs)
- [`gateway/pi-web/tests/test_identity_api.py`](../../gateway/pi-web/tests/test_identity_api.py)
- [`gateway/pi-web/tests/test_identity_dom.cjs`](../../gateway/pi-web/tests/test_identity_dom.cjs)
- [`gateway/pi-web/tests/test_identity_quarantine.py`](../../gateway/pi-web/tests/test_identity_quarantine.py)
- [`gateway/pi-web/tests/test_lora_availability.py`](../../gateway/pi-web/tests/test_lora_availability.py)
- [`gateway/pi-web/tests/test_lora_availability_ui.cjs`](../../gateway/pi-web/tests/test_lora_availability_ui.cjs)
- [`gateway/pi-web/tests/test_network_refresh_ui.cjs`](../../gateway/pi-web/tests/test_network_refresh_ui.cjs)
- [`gateway/pi-web/tests/test_node_config_messages_ui.cjs`](../../gateway/pi-web/tests/test_node_config_messages_ui.cjs)
- [`gateway/pi-web/tests/test_node_maintenance_api.py`](../../gateway/pi-web/tests/test_node_maintenance_api.py)
- [`gateway/pi-web/tests/test_supervision.py`](../../gateway/pi-web/tests/test_supervision.py)
- [`gateway/pi-web/web_service.py`](../../gateway/pi-web/web_service.py)

## Servidor

- [`server/consumer/README.md`](../../server/consumer/README.md)
- [`server/consumer/consumer_service.py`](../../server/consumer/consumer_service.py)
- [`server/consumer/identity_ingest.py`](../../server/consumer/identity_ingest.py)
- [`server/consumer/tests/identity_pg_bridge.mjs`](../../server/consumer/tests/identity_pg_bridge.mjs)
- [`server/consumer/tests/test_physical_ingest.py`](../../server/consumer/tests/test_physical_ingest.py)
- [`server/consumer/tests/test_supervision.py`](../../server/consumer/tests/test_supervision.py)
- [`server/db/migrations/006_physical_identity.sql`](../../server/db/migrations/006_physical_identity.sql)
- [`server/install_identity.sh`](../../server/install_identity.sh)
- [`server/lib/database.sh`](../../server/lib/database.sh)

## Protocolo y documentación

- [`shared/protocol/batch-format.md`](../../shared/protocol/batch-format.md)
- [`shared/protocol/db-schema.md`](../../shared/protocol/db-schema.md)
- [`shared/protocol/frame-format.md`](../../shared/protocol/frame-format.md)
- [`shared/protocol/identity-files.md`](../../shared/protocol/identity-files.md)
- [`shared/protocol/identity-safety.md`](../../shared/protocol/identity-safety.md)
- [`shared/protocol/node-config.md`](../../shared/protocol/node-config.md)
- [`shared/protocol/package_identity.py`](../../shared/protocol/package_identity.py)

## Fuera del repositorio

Se añade una entrada técnica a `TFM/bitacora.md`. El paquete de instalación de la VM se prepara en `/private/tmp/modulinkr-identity-server.tar.gz`; los paquetes y los registros temporales de comprobación no pertenecen al repositorio.
