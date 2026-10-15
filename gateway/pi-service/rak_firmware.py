"""Política de firmware RAK compartida por mantenimiento y alta USB."""

import json
from pathlib import Path
import re

POLICY_PATH = Path(__file__).with_name("rak_policy.json")


def version_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", value):
        raise ValueError("La versión del RAK no tiene un formato válido.")
    result = tuple(int(part) for part in value.split("."))
    if max(result) > 65535:
        raise ValueError("La versión del RAK excede el formato admitido.")
    return result


def load_policy(path=POLICY_PATH):
    policy = json.loads(Path(path).read_text())
    if policy.get("schema_version") != 1 or policy.get("model") != "RAK3172-E":
        raise ValueError("La política no corresponde al RAK3172-E.")
    target = policy["target_version"]
    approved = policy["approved_versions"]
    if not isinstance(approved, list) or not approved or target not in approved:
        raise ValueError("La versión de destino debe estar aprobada.")
    for value in approved:
        version_number(value)
    if version_number(policy["upgrade_floor"]) > version_number(target):
        raise ValueError("El límite de actualización supera la versión de destino.")
    image = policy["image"]
    if image["version"] != f"RUI_{target}_{policy['model']}":
        raise ValueError("La imagen no coincide con la política.")
    if not re.fullmatch(r"[0-9a-f]{64}", image["sha256"]) or type(image["size"]) is not int or not 8 <= image["size"] <= 245760:
        raise ValueError("La identidad de la imagen no está fijada.")
    if not image["url"].startswith("https://downloads.rakwireless.com/RUI/RUI3/Image/"):
        raise ValueError("La imagen no utiliza la fuente oficial.")
    return policy


def classify(raw_version, policy=None):
    policy = policy or load_policy()
    match = re.fullmatch(r"RUI_([0-9]+\.[0-9]+\.[0-9]+)_(RAK3172-[A-Z]+)", str(raw_version or ""))
    state = "unknown"
    number = None
    model = None
    if match:
        number, model = match.groups()
        try:
            parsed = version_number(number)
        except ValueError:
            return {"model": model, "version": number, "raw_version": raw_version,
                    "state": state, "approved": False, "can_update": False,
                    "target_version": policy["target_version"]}
        if model != policy["model"]:
            state = "wrong_model"
        elif number in policy["approved_versions"]:
            state = "approved"
        elif parsed > version_number(policy["target_version"]):
            state = "newer_unvalidated"
        elif parsed >= version_number(policy["upgrade_floor"]):
            state = "update_required"
        else:
            state = "unsupported"
    return {"model": model, "version": number, "raw_version": raw_version,
            "state": state, "approved": state == "approved",
            "can_update": state == "update_required",
            "target_version": policy["target_version"]}
