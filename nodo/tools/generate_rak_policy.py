"""Genera las constantes del nodo desde la política de RAK del proyecto."""

import json
from pathlib import Path
import sys


def generate(project):
    service = project.parent / "gateway/pi-service"
    sys.path.insert(0, str(service))
    from rak_firmware import load_policy
    policy = load_policy(service / "rak_policy.json")
    image = policy["image"]
    quoted = lambda value: json.dumps(value, ensure_ascii=True)
    sha = ",".join("0x" + image["sha256"][at:at + 2] for at in range(0, 64, 2))
    approved = ",".join(quoted(v) for v in policy["approved_versions"])
    header = f'''#pragma once
#include <cstddef>
#include <cstdint>
namespace rakpolicy {{
inline constexpr char model[] = {quoted(policy['model'])};
inline constexpr char target[] = {quoted(policy['target_version'])};
inline constexpr char floor[] = {quoted(policy['upgrade_floor'])};
inline constexpr char image_version[] = {quoted(image['version'])};
inline constexpr size_t image_size = {image['size']};
inline constexpr uint8_t image_sha[32] = {{{sha}}};
inline constexpr const char* approved[] = {{{approved}}};
}}
'''
    output = project / ".pio/generated"
    output.mkdir(parents=True, exist_ok=True)
    path = output / "rak_policy.h"
    if not path.exists() or path.read_text() != header:
        path.write_text(header)
    return output


if "Import" in globals():
    Import("env")
    env.Append(CPPPATH=[str(generate(Path(env.subst("$PROJECT_DIR"))))])
elif __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1])
