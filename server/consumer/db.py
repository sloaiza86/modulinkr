#!/usr/bin/env python3
"""ModuLinkr, conexión a PostgreSQL del consumidor cloud.

Envoltorio mínimo sobre psycopg2: una conexión, transacción por mensaje
(commit/rollback los maneja el llamador) y reconexión perezosa si la
conexión se cae (reinicio de PostgreSQL, corte). Sin pool: el volumen del
despliegue (mensajes por segundo en el peor caso) no lo necesita.

Config por variables de entorno (el instalador las escribe en
/etc/modulinkr/consumer.env):
  MODULINKR_DB_HOST      (default 127.0.0.1)
  MODULINKR_DB_PORT      (default 5432)
  MODULINKR_DB_NAME      (default modulinkr)
  MODULINKR_DB_USER      (default modulinkr)
  MODULINKR_DB_PASSWORD
"""

from __future__ import annotations

import logging
import os

import psycopg2

LOG = logging.getLogger("modulinkr.db")


class Db:
    def __init__(self):
        self.host     = os.environ.get("MODULINKR_DB_HOST", "127.0.0.1")
        self.port     = int(os.environ.get("MODULINKR_DB_PORT", "5432"))
        self.name     = os.environ.get("MODULINKR_DB_NAME", "modulinkr")
        self.user     = os.environ.get("MODULINKR_DB_USER", "modulinkr")
        self.password = os.environ.get("MODULINKR_DB_PASSWORD", "")
        self._conn = None

    def conn(self):
        """Devuelve la conexión, abriéndola o reabriéndola si hace falta."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self.host, port=self.port, dbname=self.name,
                user=self.user, password=self.password,
                connect_timeout=10,
            )
            self._conn.autocommit = False
            LOG.info("PostgreSQL conectado (%s@%s/%s)",
                     self.user, self.host, self.name)
        return self._conn

    def rollback(self) -> None:
        """Rollback tolerante: si la conexión murió, se descarta y la
        siguiente operación reconecta."""
        try:
            if self._conn is not None and not self._conn.closed:
                self._conn.rollback()
        except Exception:                            # noqa: BLE001
            self._conn = None
