#!/usr/bin/env python3
"""Comprueba contratos y convenciones de los mensajes no web de ModuLinkr."""

from __future__ import annotations

import ast
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, fragment: str, failures: list[str]) -> None:
    if fragment not in read(path):
        failures.append(f"{path}: missing protected fragment {fragment!r}")


def check_contracts(failures: list[str]) -> None:
    require("gateway/heltec-radio/src/main.cpp",
            "[rx] #%lu len=%u rssi=%.1f snr=%.1f hex=", failures)
    require("gateway/heltec-radio/src/main.cpp",
            "[tx] ok len=%u total=%lu", failures)
    require("gateway/pi-service/gateway_service.py",
            "modbus-debug origin=%s mode=%s", failures)
    require("nodo/src/commission.cpp", 'respond("CFG:READY")', failures)
    require("nodo/src/commission.cpp", '"CFG:HELLO %s"', failures)
    require("nodo/src/commission.cpp", '"CFG:DATA "', failures)
    require("gateway/pi-service/get_net.sh", "printf '%s=%s\\n'", failures)


def check_secret_redaction(failures: list[str]) -> None:
    source = read("nodo/src/nbiot.cpp")
    start = source.index("bool Nbiot::mqttConnect")
    end = source.index("bool Nbiot::mqttIsConnected", start)
    mqtt_connect = source[start:end]
    if mqtt_connect.count("<redacted>") < 2:
        failures.append("nodo/src/nbiot.cpp: MQTT credentials are not redacted")
    guarded_log = re.search(
        r"if \(have_auth\).*?<redacted>.*?\} else \{.*?"
        r"Serial\.printf\(\"\[at\] >> %s\\n\", cmd\);",
        mqtt_connect,
        re.DOTALL,
    )
    if guarded_log is None:
        failures.append("nodo/src/nbiot.cpp: MQTT command logging is not guarded by redaction")


def check_shell_prompts(failures: list[str]) -> None:
    forbidden = ("[y/N]", "[Y/n]", "¿Qué se desea", "Reejecutar",
                 "Reintentar", "Ejecutar con sudo")
    for path in ROOT.glob("**/*.sh"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                failures.append(f"{path.relative_to(ROOT)}: legacy prompt token {token!r}")


def check_python_logs(failures: list[str]) -> None:
    spanish = re.compile(
        r"\b(fallo|fallido|invalido|descartad[oa]|conectad[oa]|"
        r"desconectad[oa]|migracion|configuracion|esperando|reintentando)\b",
        re.IGNORECASE,
    )
    levels = {"debug", "info", "warning", "error", "exception", "critical"}
    for base in ("gateway", "server"):
        for path in (ROOT / base).glob("**/*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in levels or not node.args:
                    continue
                message = node.args[0]
                if isinstance(message, ast.Constant) and isinstance(message.value, str):
                    if spanish.search(message.value):
                        failures.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: Spanish technical log"
                        )


def split_cpp_arguments(source: str) -> list[str]:
    arguments: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(source):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append(source[start:index].strip())
            start = index + 1
    arguments.append(source[start:].strip())
    return arguments


def serial_printf_calls(source: str) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []
    marker = "Serial.printf("
    offset = 0
    while True:
        start = source.find(marker, offset)
        if start < 0:
            return calls
        index = start + len(marker)
        depth = 1
        quote = ""
        escaped = False
        while index < len(source) and depth:
            char = source[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in ('"', "'"):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth:
            calls.append((source.count("\n", 0, start) + 1, ""))
            return calls
        calls.append((source.count("\n", 0, start) + 1,
                      source[start + len(marker):index - 1]))
        offset = index


def check_serial_printf(failures: list[str]) -> None:
    specifier = re.compile(
        r"%(?!%)(?:[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|z|t|j)?"
        r"[diuoxXfFeEgGaAcsp])"
    )
    string_literal = re.compile(r'"(?:\\.|[^"\\])*"')
    for base in ("nodo/src", "gateway/heltec-radio/src"):
        for path in (ROOT / base).glob("**/*.cpp"):
            source = path.read_text(encoding="utf-8")
            for line, call in serial_printf_calls(source):
                if not call:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{line}: unterminated Serial.printf call"
                    )
                    continue
                arguments = split_cpp_arguments(call)
                literals = string_literal.findall(arguments[0])
                if not literals:
                    continue
                message = "".join(literal[1:-1] for literal in literals)
                expected = len(specifier.findall(message.replace("%%", "")))
                actual = len(arguments) - 1
                if expected != actual:
                    failures.append(
                        f"{path.relative_to(ROOT)}:{line}: Serial.printf expects "
                        f"{expected} arguments, received {actual}"
                    )


def main() -> int:
    failures: list[str] = []
    check_contracts(failures)
    check_secret_redaction(failures)
    check_shell_prompts(failures)
    check_python_logs(failures)
    check_serial_printf(failures)
    if failures:
        for failure in failures:
            print(f"[ERROR] {failure}", file=sys.stderr)
        return 1
    print("[ OK ] Message conventions and protected contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
