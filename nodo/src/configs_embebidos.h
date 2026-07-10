// ModuLinkr, configs embebidos (fase 1 del comisionamiento)
//
// Un config.json completo por unidad física del banco, conforme a
// node-config.md schema 2.0. El build_flag NODE_CONFIG (platformio.ini)
// selecciona cuál se embebe:
//
//   NODE_CONFIG=1  nodo 1, lejano, XY-MD02 (T/H ambiente) por Modbus
//   NODE_CONFIG=2  nodo 2, supernodo con NB-IoT (SIM7028) y WitMotion
//                  WT901C485 (IMU, aceleración XYZ) por Modbus
//
// La fase 2 (carga desde flash) sustituirá este archivo por la lectura de
// LittleFS/NVS sin tocar config.cpp.

#pragma once

#if !defined(NODE_CONFIG)
#error "Falta definir NODE_CONFIG (1 o 2) en platformio.ini"
#endif

#if NODE_CONFIG == 1

static const char kConfigJson[] = R"json({
  "schema_version": "2.0",
  "node": {
    "id":          1,
    "type":        "node",
    "name":        "Nodo lejano banco TFM",
    "description": "Atom Lite + DTU EU868 + XY-MD02"
  },
  "transport": {
    "lora": {
      "region":           "EU868",
      "frequency_hz":     869525000,
      "sf":               7,
      "bw_khz":           125,
      "tx_power_dbm":     10,
      "network_id":       1,
      "send_interval_ms": 5000,
      "ack_enabled":      true,
      "ack_timeout_ms":   3000,
      "max_retries":      2
    },
    "mesh": {
      "relay_enabled":        true,
      "max_ttl":              4,
      "beacon_timeout_ms":    90000,
      "parent_min_rssi":      -100,
      "parent_hysteresis_db": 6,
      "parent_missed_frames": 3,
      "sn_offer_wait_ms":     1000
    }
  },
  "modbus": {
    "baudrate": 9600,
    "parity":   "N",
    "stopbits": 1,
    "devices": [
      {
        "name":             "amb",
        "description":      "XY-MD02 ambiente",
        "addressing": {
          "default_slave_id": 1,
          "desired_slave_id": 1
        },
        "poll_interval_ms": 5000,
        "reads": [
          { "id": "temp", "name": "temperature", "function": "read_input_registers",
            "address": 1, "type": "int16",  "scale": 0.1, "unit": "C" },
          { "id": "hum",  "name": "humidity",    "function": "read_input_registers",
            "address": 2, "type": "uint16", "scale": 0.1, "unit": "%RH" }
        ]
      }
    ]
  }
})json";

#elif NODE_CONFIG == 2

static const char kConfigJson[] = R"json({
  "schema_version": "2.0",
  "node": {
    "id":          2,
    "type":        "super_node",
    "name":        "Supernodo banco TFM",
    "description": "Atom Lite + DTU EU868 + NB-IoT 2 Unit (SIM7028) + WT901C485"
  },
  "transport": {
    "lora": {
      "region":           "EU868",
      "frequency_hz":     869525000,
      "sf":               7,
      "bw_khz":           125,
      "tx_power_dbm":     10,
      "network_id":       1,
      "send_interval_ms": 5000,
      "ack_enabled":      true,
      "ack_timeout_ms":   3000,
      "max_retries":      2
    },
    "mesh": {
      "relay_enabled":        true,
      "max_ttl":              4,
      "beacon_timeout_ms":    90000,
      "parent_min_rssi":      -100,
      "parent_hysteresis_db": 6,
      "parent_missed_frames": 3,
      "sn_offer_wait_ms":     1000
    },
    "nbiot": {
      "apn":                  "nb.wlapn.com",
      "apn_user":             "ENERBOSS",
      "apn_pass":             "ENERBOSS",
      "mqtt_broker":          "broker.hivemq.com",
      "mqtt_port":            1883,
      "tls":                  false,
      "topic_telemetry":      "modulinkr/v1/{node_id}/batch",
      "topic_commands":       "modulinkr/v1/{node_id}/cmd",
      "failover_missed_acks": 5,
      "failover_window_ms":   30000,
      "relay_enabled":        true,
      "relay_queue_max":      128
    }
  },
  "modbus": {
    "baudrate": 9600,
    "parity":   "N",
    "stopbits": 1,
    "devices": [
      {
        "name":             "imu",
        "description":      "WitMotion WT901C485, aceleracion XYZ",
        "addressing": {
          "default_slave_id": 80,
          "desired_slave_id": 80
        },
        "poll_interval_ms": 1000,
        "reads": [
          { "id": "ax", "name": "accel_x", "function": "read_holding_registers",
            "address": 52, "type": "int16", "scale": 0.000488, "unit": "g" },
          { "id": "ay", "name": "accel_y", "function": "read_holding_registers",
            "address": 53, "type": "int16", "scale": 0.000488, "unit": "g" },
          { "id": "az", "name": "accel_z", "function": "read_holding_registers",
            "address": 54, "type": "int16", "scale": 0.000488, "unit": "g" }
        ]
      }
    ]
  }
})json";

#else
#error "NODE_CONFIG debe ser 1 o 2"
#endif
