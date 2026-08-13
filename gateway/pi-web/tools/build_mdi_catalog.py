#!/usr/bin/env python3
"""Genera el catálogo MDI local y dividido en bloques para el visor."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from xml.etree import ElementTree


TAMANO_BLOQUE = 192


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("fuente", type=Path, help="Directorio extraído de @mdi/svg")
    parser.add_argument(
        "--salida",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "static" / "mdi",
    )
    return parser.parse_args()


def leer_icono(ruta: Path) -> str:
    raiz = ElementTree.parse(ruta).getroot()
    caminos = [elemento.attrib["d"] for elemento in raiz.iter()
               if elemento.tag.endswith("path") and "d" in elemento.attrib]
    if len(caminos) != 1:
        raise ValueError(f"{ruta.name}: se esperaba un único path y hay {len(caminos)}")
    return caminos[0]


def main() -> None:
    args = leer_argumentos()
    paquete = json.loads((args.fuente / "package.json").read_text(encoding="utf-8"))
    rutas = sorted((args.fuente / "svg").glob("*.svg"), key=lambda ruta: ruta.stem)
    if not rutas:
        raise SystemExit("No se encontraron iconos SVG")

    salida = args.salida
    salida.mkdir(parents=True, exist_ok=True)
    for anterior in salida.glob("*.json"):
        anterior.unlink()

    bloques = []
    for numero, inicio in enumerate(range(0, len(rutas), TAMANO_BLOQUE)):
        grupo = rutas[inicio:inicio + TAMANO_BLOQUE]
        archivo = f"{numero:03d}.json"
        contenido = {ruta.stem: leer_icono(ruta) for ruta in grupo}
        (salida / archivo).write_text(
            json.dumps(contenido, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        bloques.append({"file": archivo, "first": grupo[0].stem, "last": grupo[-1].stem})

    manifiesto = {
        "source": paquete["name"],
        "version": paquete["version"],
        "license": paquete["license"],
        "icons": len(rutas),
        "chunkSize": TAMANO_BLOQUE,
        "chunks": bloques,
    }
    (salida / "manifest.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    shutil.copyfile(args.fuente / "LICENSE", salida / "LICENSE")
    print(f"MDI {paquete['version']}: {len(rutas)} iconos en {len(bloques)} bloques")


if __name__ == "__main__":
    main()
