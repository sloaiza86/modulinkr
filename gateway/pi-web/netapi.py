"""ModuLinkr, API de parámetros de red LoRa del gateway (visor web).

Router de la página "Configurar red LoRa": edición de los parámetros que
todo el despliegue comparte (network_id, región, frecuencia, SF, BW, TTL y
seguridad AES-CCM). Los consume el servicio del gateway, un proceso aparte
que los lee de gateway.env al arrancar; por eso guardarlos pasa por el
script privilegiado set_net.sh (regla sudoers del instalador), que reescribe
gateway.env y reinicia el servicio. Al reiniciar, el servicio reempuja los
parámetros de radio al Heltec (comando RADIO), así que network_id,
frecuencia, SF y BW se aplican en caliente sin reflashear (camino B).

Endpoints (auth de sesión aplicada al incluir el router):
  POST /api/net/guardar             valida y aplica (set_net.sh, reinicia)
  POST /api/net/migracion           programa el cambio coordinado (§17.8)
  GET  /api/net/migracion           estado, cuenta atrás y pase de lista
  POST /api/net/migracion/cerrar    cierra o aborta la operación

Los valores actuales para rellenar el formulario los sirve GET
/api/config/red (configapi), que ya los lee de gateway.env con get_net.sh.

Dos caminos para lo mismo, y la diferencia importa. `guardar` cambia los
parámetros AHORA: es lo correcto cuando no hay nodos que perder (un banco,
un despliegue que aún no existe) y deja incomunicado a cualquiera que siga
con los viejos. `migracion` es el procedimiento para una red viva: reparte
el config nuevo a los nodos con una hora de salto acordada, salta a esa hora
con ellos, y luego vuelve periódicamente a los viejos para recoger a los que
se quedaron. El primero se conserva porque sigue siendo el camino sensato
cuando la red cabe en una mesa.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.net")

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_NET_SH = SERVICE_DIR / "set_net.sh"
GET_NET_SH = SERVICE_DIR / "get_net.sh"
DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

REGIONS = {"EU868", "US915", "CN470", "AS923"}
BW_KHZ = {125, 250, 500}

# Márgenes de la hora del salto.
#
# El mínimo no es un capricho de interfaz: entre programar y saltar hay que
# repartir el config a todos los nodos, y cada entrega es una transferencia
# por radio con su ventana de escucha. Cinco minutos es lo mínimo para que
# quepan unas pocas; el visor debería proponer bastante más según el censo.
# El máximo evita el salto olvidado, programado un lunes y ejecutado un
# viernes sobre una red que ya nadie recuerda haber tocado.
MIG_MIN_ADELANTO_S = 300
MIG_MAX_ADELANTO_S = 7 * 24 * 3600

router = APIRouter(prefix="/api/net")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=5.0)


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _sudo_stdin(script: Path, stdin: str, timeout_s: float) -> tuple[bool, str]:
    """Ejecuta el script privilegiado con sudo no interactivo, pasando los
    pares KEY=VALUE por stdin (no por argumentos, para que la clave de red no
    asome en la lista de procesos). Devuelve (ok, salida)."""
    if not script.is_file():
        return False, f"{script.name} no está junto a pi-service"
    try:
        r = subprocess.run(["sudo", "-n", str(script)], input=stdin,
                           capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "la operación excedió el tiempo máximo"
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and "password is required" in out:
        return False, ("sudo sin regla para el visor: reejecutar el "
                       "instalador (sudoers de modulinkr-web)")
    return r.returncode == 0, out


def _parse_body(body: dict) -> tuple[list[str], str | None]:
    """Normaliza y valida el cuerpo del formulario. Devuelve (lines, err):
    con err no None, lines es indefinido y el llamador responde 400. lines
    son los pares KEY=VALUE que set_net.sh reescribe en gateway.env."""
    def _int(key, lo, hi, name):
        try:
            v = int(body.get(key))
        except (TypeError, ValueError):
            return None, f"{name} inválido"
        if not (lo <= v <= hi):
            return None, f"{name} fuera de rango ({lo}-{hi})"
        return v, None

    network_id, err = _int("network_id", 1, 254, "ID de red")
    if err:
        return [], err
    freq, err = _int("frequency_hz", 100_000_000, 1_000_000_000, "frecuencia")
    if err:
        return [], err
    sf, err = _int("sf", 7, 12, "SF")
    if err:
        return [], err
    max_ttl, err = _int("max_ttl", 1, 15, "Max TTL")
    if err:
        return [], err
    try:
        bw = int(body.get("bw_khz"))
    except (TypeError, ValueError):
        return [], "BW inválido"
    if bw not in BW_KHZ:
        return [], "BW no admitido (125, 250 o 500)"
    region = str(body.get("region", "")).strip()
    if region not in REGIONS:
        return [], "región no admitida"

    sec_enabled = bool(body.get("security_enabled", False))
    sec_key = str(body.get("security_key", "")).strip()
    if sec_enabled:
        if len(sec_key) != 32 or any(c not in "0123456789abcdefABCDEF" for c in sec_key):
            return [], "seguridad activa exige clave de red de 32 hex"

    lines = [
        f"MODULINKR_LORA_REGION={region}",
        f"MODULINKR_LORA_FREQ_HZ={freq}",
        f"MODULINKR_NETWORK_ID={network_id}",
        f"MODULINKR_SF={sf}",
        f"MODULINKR_BW_KHZ={bw}",
        f"MODULINKR_MAX_TTL={max_ttl}",
        f"MODULINKR_SEC_ENABLED={'1' if sec_enabled else '0'}",
    ]
    # La clave solo se reescribe si se dio una (con seguridad activa es
    # obligatoria; desactivada, en blanco conserva la vigente).
    if sec_key:
        lines.append(f"MODULINKR_SEC_KEY={sec_key}")
    return lines, None


@router.post("/guardar")
async def guardar(request: Request):
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    lines, err = _parse_body(body)
    if err:
        return _err(400, err)

    stdin = "\n".join(lines) + "\n"
    ok, out = _sudo_stdin(SET_NET_SH, stdin, timeout_s=40)
    if not ok:
        LOG.warning("guardar red fallido: %s", out)
        return _err(502, out)
    LOG.info("red LoRa reconfigurada (network_id=%s freq=%s sf=%s bw=%s)",
             body.get("network_id"), body.get("frequency_hz"),
             body.get("sf"), body.get("bw_khz"))
    return {"ok": True, "output": out}


# ----- Cambio coordinado de parámetros de red (§17.8, fase C3) -----


def _perfil_vigente() -> tuple[dict, str | None]:
    """Los parámetros que el gateway usa ahora, leídos de gateway.env.

    Es la misma fuente que rellena el formulario, y coincide con lo que el
    servicio tiene cargado siempre que no haya una operación a medias, que es
    justo lo que este endpoint se niega a permitir.
    """
    if not GET_NET_SH.is_file():
        return {}, "get_net.sh no está junto a pi-service"
    ok, txt = _sudo_stdin(GET_NET_SH, "", timeout_s=15)
    if not ok:
        return {}, f"no se pudieron leer los parámetros vigentes: {txt}"
    env = {}
    for line in txt.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    try:
        return {
            "network_id": int(env["MODULINKR_NETWORK_ID"]),
            "freq_hz":    int(env["MODULINKR_LORA_FREQ_HZ"]),
            "sf":         int(env["MODULINKR_SF"]),
            "bw_khz":     int(env["MODULINKR_BW_KHZ"]),
            "max_ttl":    int(env["MODULINKR_MAX_TTL"]),
            "sec_key":    (env.get("MODULINKR_SEC_KEY", "")
                           if env.get("MODULINKR_SEC_ENABLED") == "1" else ""),
        }, None
    except (KeyError, ValueError) as e:
        return {}, f"gateway.env incompleto o ilegible ({e})"


@router.post("/migracion")
async def migracion_crear(request: Request):
    """Programa el cambio coordinado de parámetros de red.

    No cambia nada todavía y no toca gateway.env: solo deja escrita la
    operación en el punto de encuentro con el servicio del gateway. A partir
    de aquí el visor reparte el config nuevo a cada nodo con este mismo
    `apply_at`, y en esa hora saltan todos a la vez, el gateway incluido.

    Se devuelve el `apply_at` calculado para que el reparto use exactamente
    ese número. Que la hora del salto salga de un solo sitio es lo que evita
    el fallo más tonto posible: nodos citados a una hora y gateway a otra.
    """
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")

    lines, err = _parse_body(body)     # misma validación que el camino directo
    if err:
        return _err(400, err)

    try:
        apply_at = int(body.get("apply_at"))
    except (TypeError, ValueError):
        return _err(400, "apply_at inválido")
    ahora = int(time.time())
    if apply_at - ahora < MIG_MIN_ADELANTO_S:
        return _err(400, f"el salto debe programarse con al menos "
                         f"{MIG_MIN_ADELANTO_S // 60} minutos de antelación, "
                         f"que es lo que tarda el reparto")
    if apply_at - ahora > MIG_MAX_ADELANTO_S:
        return _err(400, "el salto no puede programarse a más de una semana")

    viejo, err = _perfil_vigente()
    if err:
        return _err(502, err)

    nuevo = dict(viejo)
    nuevo.update({
        "network_id": int(body["network_id"]),
        "freq_hz":    int(body["frequency_hz"]),
        "sf":         int(body["sf"]),
        "bw_khz":     int(body["bw_khz"]),
        "max_ttl":    int(body["max_ttl"]),
    })
    if body.get("security_enabled"):
        clave = str(body.get("security_key", "")).strip()
        nuevo["sec_key"] = clave or viejo["sec_key"]
    else:
        nuevo["sec_key"] = ""

    if nuevo == viejo:
        return _err(400, "los parámetros nuevos son idénticos a los vigentes")

    win_s = int(body.get("recov_win_s", 15))
    per_s = int(body.get("recov_per_s", 300))
    horas = float(body.get("recov_h", 24))
    if not 5 <= win_s <= 120:
        return _err(400, "recov_win_s fuera de 5-120")
    if not win_s * 4 <= per_s <= 3600:
        return _err(400, "recov_per_s fuera de rango (al menos cuatro veces "
                         "la ventana, y como mucho una hora)")
    if not 0 < horas <= 168:
        return _err(400, "recov_h fuera de 0-168")
    recov_until = apply_at + int(horas * 3600)

    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id, state FROM net_migration
                    WHERE state IN ('programada', 'saltada')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": "ya hay una operación en curso; "
                                      "ciérrala o abórtala antes de programar otra",
                             "id": fila[0], "state": fila[1]})
            cur = c.execute(
                """INSERT INTO net_migration
                       (apply_at, old_profile, new_profile, state, recov_win_s,
                        recov_per_s, recov_until, created_ts)
                   VALUES (?, ?, ?, 'programada', ?, ?, ?, ?)""",
                (apply_at, json.dumps(viejo), json.dumps(nuevo),
                 win_s, per_s, recov_until, time.time()))
            c.commit()
            mig_id = cur.lastrowid
    except sqlite3.Error as e:
        return _err(503, f"buffer no disponible: {e}")

    LOG.info("migracion de red %d programada para epoch %d (%s)",
             mig_id, apply_at, nuevo)
    return {"id": mig_id, "apply_at": apply_at, "recov_until": recov_until,
            "old_profile": viejo, "new_profile": nuevo}


@router.get("/migracion")
def migracion_estado():
    """Estado de la operación viva, con lo que el panel necesita para pintarla.

    El pase de lista sale de en qué mundo se ha oído a cada nodo después del
    salto. Tres respuestas y no dos: migrado, rezagado, y sin noticias. La
    tercera es la que hay que poder distinguir para no dar por perdido a un
    nodo que solo llevaba un rato callado.
    """
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id, apply_at, old_profile, new_profile, state,
                          recov_win_s, recov_per_s, recov_until, detail
                     FROM net_migration
                    WHERE state IN ('programada', 'saltada')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is None:
                return {"activa": False}
            mig_id = fila[0]
            vistos = c.execute(
                """SELECT node_id, profile, ts FROM net_migration_seen
                    WHERE migration_id = ? ORDER BY node_id""",
                (mig_id,)).fetchall()
    except sqlite3.Error as e:
        return _err(503, f"buffer no disponible: {e}")

    ahora = int(time.time())
    apply_at, state = fila[1], fila[4]
    win_s, per_s, recov_until = fila[5], fila[6], fila[7]

    roll: dict[int, dict] = {}
    for node_id, profile, ts in vistos:
        e = roll.setdefault(int(node_id), {"node_id": int(node_id),
                                           "nuevo": None, "viejo": None})
        e[profile] = ts
    nodos = []
    for e in roll.values():
        # Un nodo con rastro en los dos mundos migró y luego revirtió, o al
        # revés. Manda el más reciente, que es donde está ahora.
        if e["nuevo"] and (not e["viejo"] or e["nuevo"] >= e["viejo"]):
            e["estado"] = "migrado"
        elif e["viejo"]:
            e["estado"] = "rezagado"
        else:
            e["estado"] = "sin noticias"
        nodos.append(e)

    out = {
        "activa": True, "id": mig_id, "state": state,
        "apply_at": apply_at, "faltan_s": max(0, apply_at - ahora),
        "old_profile": json.loads(fila[2]), "new_profile": json.loads(fila[3]),
        "recov_win_s": win_s, "recov_per_s": per_s,
        "recov_until": recov_until, "detail": fila[8],
        "nodos": nodos,
    }
    if state == "saltada":
        fase = (ahora - apply_at) % per_s
        out["mundo"] = "viejo" if fase < win_s else "nuevo"
        out["proximo_cambio_s"] = (win_s - fase) if fase < win_s else (per_s - fase)
        out["recuperacion_restante_s"] = max(0, recov_until - ahora)
    return out


@router.post("/migracion/cerrar")
def migracion_cerrar(body: dict = Body(default={})):
    """Termina la operación. Con `abortar`, antes del salto; sin él, después.

    Cerrar tras el salto escribe los parámetros nuevos en gateway.env, que es
    lo que convierte el cambio en el estado normal de la instalación: a partir
    de ahí un reinicio del servicio arranca ya en el mundo nuevo sin depender
    de que quede rastro de la operación.

    Abortar antes del salto no deshace nada porque nada ha cambiado todavía,
    que es la propiedad que hace segura toda esta forma de trabajar: hasta la
    hora del salto, la operación se puede tirar a la basura sin consecuencias.
    """
    abortar = bool(body.get("abortar", False))
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id, state, new_profile FROM net_migration
                    WHERE state IN ('programada', 'saltada')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is None:
                return _err(404, "no hay ninguna operación en curso")
            mig_id, state, nuevo_json = fila
            if abortar and state == "saltada":
                return _err(409, "el salto ya se ejecutó: la operación se "
                                 "cierra, no se aborta")
            c.execute(
                """UPDATE net_migration SET state = ?, detail = ?, updated_ts = ?
                    WHERE id = ?""",
                ("abortada" if abortar else "cerrada",
                 "abortada desde el visor antes del salto" if abortar
                 else "cerrada desde el visor",
                 time.time(), mig_id))
            c.commit()
    except sqlite3.Error as e:
        return _err(503, f"buffer no disponible: {e}")

    if abortar:
        LOG.info("migracion de red %d abortada antes del salto", mig_id)
        return {"ok": True, "id": mig_id, "state": "abortada"}

    nuevo = json.loads(nuevo_json)
    lines = [
        f"MODULINKR_LORA_FREQ_HZ={nuevo['freq_hz']}",
        f"MODULINKR_NETWORK_ID={nuevo['network_id']}",
        f"MODULINKR_SF={nuevo['sf']}",
        f"MODULINKR_BW_KHZ={nuevo['bw_khz']}",
        f"MODULINKR_MAX_TTL={nuevo['max_ttl']}",
        f"MODULINKR_SEC_ENABLED={'1' if nuevo.get('sec_key') else '0'}",
    ]
    if nuevo.get("sec_key"):
        lines.append(f"MODULINKR_SEC_KEY={nuevo['sec_key']}")
    ok, out = _sudo_stdin(SET_NET_SH, "\n".join(lines) + "\n", timeout_s=40)
    if not ok:
        # La operación queda cerrada igualmente: el servicio ya está en los
        # parámetros nuevos y volver atrás aquí sería peor. Lo que falta es
        # dejarlo escrito, y eso se dice para que se arregle a mano.
        LOG.warning("migracion %d cerrada pero gateway.env NO actualizado: %s",
                    mig_id, out)
        return _err(502, f"operación cerrada, pero gateway.env no se pudo "
                         f"actualizar: {out}. El servicio sigue en los "
                         f"parámetros nuevos hasta que se reinicie.")
    LOG.info("migracion de red %d cerrada y fijada en gateway.env", mig_id)
    return {"ok": True, "id": mig_id, "state": "cerrada", "output": out}
