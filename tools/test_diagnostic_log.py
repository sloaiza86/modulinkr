"""Comprueba el emisor C++ real con salida serie y reloj de escritorio."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HEADER = Path(__file__).resolve().parents[1] / "shared" / "diagnostic_log.h"


class DiagnosticLogTests(unittest.TestCase):
    def test_atomic_records_clocks_sanitizing_and_truncation(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Arduino.h").write_text('''#pragma once
#include <cstdint>
#include <string>
#include <vector>
struct Console {
    std::vector<std::string> records;
    size_t write(const uint8_t* p, size_t n) {
        records.emplace_back(reinterpret_cast<const char*>(p), n); return n;
    }
};
inline Console Serial;
''')
            (root / "esp_timer.h").write_text('''#pragma once
#include <cstdint>
inline int64_t esp_timer_get_time() { return 123456789; }
''')
            source = root / "test.cpp"
            source.write_text('''#include "diagnostic_log.h"
#include <cassert>
#include <algorithm>
uint32_t epoch() { return 1788800400; }
int main() {
    diag::log("INFO", "node.mesh", "mesh.parent_changed", "parent=%u", 255u);
    assert(Serial.records.size() == 1);
    const auto boot = Serial.records.back();
    assert(boot.find("up=0000000123.456s") == 0);
    assert(boot.find("INFO") == 21);
    assert(boot.find("node.mesh") == 30);
    assert(boot.find("event=mesh.parent_changed parent=255\\n") == 55);
    diag::epochClock = epoch;
    diag::log("WARNING", "node.modbus", "modbus.failed", "detail=%s", "bad\\r\\nFAKE\\tLOG\\x1b");
    const auto utc = Serial.records.back();
    assert(utc.find("2026-09-07T17:00:00Z") == 0);
    assert(utc.find("WARNING") == 21);
    assert(utc.find("node.modbus") == 30);
    assert(std::count(utc.begin(), utc.end(), '\\n') == 1);
    assert(utc.find('\\r') == std::string::npos);
    assert(utc.find('\\x1b') == std::string::npos);
    std::string big(3000, 'x');
    diag::log("DEBUG", "node.at", "at.response", "data=%s", big.c_str());
    assert(Serial.records.size() == 3);
    assert(Serial.records.back().size() < 1152);
    assert(Serial.records.back().find(" truncated=true\\n") != std::string::npos);
}
''')
            executable = root / "test"
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I", str(root), "-I", str(HEADER.parent), str(source),
                            "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
