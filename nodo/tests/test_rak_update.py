"""Comprueba el puente binario y los controles previos a escribir el RAK."""
from pathlib import Path
import hashlib
import importlib.util
import io
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

NODE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rak_update", NODE / "tools/update_rak.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class RakUpdateTests(unittest.TestCase):
    def test_at_ok_accepts_complete_line_at_start_without_matching_payload(self):
        for data in (b"OK\r\n", b"\r\nOK\r\n", b"AT\r\nOK\n"):
            self.assertTrue(updater.response_ok(data))
        for data in (b"NOT_OK\r\n", b"DATAOK\r\n", b"OK", b"AT_COMMAND_NOT_FOUND\r\n"):
            self.assertFalse(updater.response_ok(data))

    def test_image_requires_hash_variant_version_and_stm32_vectors(self):
        data = struct.pack("<II", 0x2000FC00, 0x08007855) + updater.VERSION.encode() + b"\0"
        with patch.object(updater, "IMAGE_SHA", hashlib.sha256(data).hexdigest()):
            self.assertEqual(updater.verified_image(data), data)
            with self.assertRaises(ValueError):
                updater.verified_image(data + b"changed")
        for invalid in (data.replace(b"RAK3172-E", b"RAK3172-T"),
                        struct.pack("<II", 0, 0) + data[8:]):
            with patch.object(updater, "IMAGE_SHA", hashlib.sha256(invalid).hexdigest()):
                with self.assertRaises(ValueError):
                    updater.verified_image(invalid)

    def test_vendor_transport_is_verified_and_does_not_execute_entrypoint(self):
        source = b"class Modem: pass\nraise RuntimeError('must not run')\n"
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode="w:gz") as archive:
            member = tarfile.TarInfo("uploader_ymodem.py")
            member.size = len(source)
            archive.addfile(member, io.BytesIO(source))
        with patch.object(updater, "TOOL_SHA", hashlib.sha256(source).hexdigest()):
            self.assertEqual(updater.official_sender(package.getvalue()).__name__, "Modem")
        with self.assertRaises(ValueError):
            updater.official_sender(package.getvalue())

    def test_wrong_device_never_enters_boot_or_transfers_firmware(self):
        class Port:
            timeout = 0
            def __init__(self): self.pending = b""; self.writes = []
            @property
            def in_waiting(self): return len(self.pending)
            def read(self, count):
                value, self.pending = self.pending[:count], self.pending[count:]
                return value
            def write(self, data):
                self.writes.append(data)
                if data == b"AT\r\n": self.pending = b"\r\nOK\r\n"
                if data == b"AT+VER=?\r\n": self.pending = b"AT+VER=RUI_4.0.6_RAK3172-T\r\n\r\nOK\r\n"
            def flush(self): pass
            def reset_input_buffer(self): self.pending = b""
        port = Port()
        with patch.object(updater.time, "sleep"):
            with self.assertRaises(ValueError):
                updater.update(port, b"not sent", None)
        self.assertNotIn(b"AT+BOOT\r\n", port.writes)

    def test_failed_transfer_can_resume_only_after_boot_confirmation(self):
        class Port:
            timeout = 0.2
            def __init__(self): self.writes = []
            def write(self, data): self.writes.append(data); return len(data)
            def flush(self): pass
            def reset_input_buffer(self): pass
        transfers = []
        class Modem:
            def __init__(self, getc, putc, mode): self.putc = putc
            def send(self, stream, info, callback):
                transfers.append(stream.read())
                callback(1, 1, 0)
                return True
        port = Port()
        with patch.object(updater.time, "sleep"), patch.object(updater, "read_until", return_value=b"AT not support\r\n"), \
             patch.object(updater, "command", return_value=b"AT+VER=" + updater.VERSION.encode() + b"\r\nOK\r\n"):
            updater.update(port, b"image", Modem, resume_boot=True)
        self.assertEqual(transfers, [b"image"])
        self.assertNotIn(b"AT+BOOT\r\n", port.writes)
        self.assertIn(b"at+update\r\n", port.writes)
        port = Port()
        with patch.object(updater, "read_until", return_value=b"OK\r\n"):
            with self.assertRaises(ValueError):
                updater.update(port, b"not sent", Modem, resume_boot=True)
        self.assertNotIn(b"at+update\r\n", port.writes)

    def test_newer_unvalidated_radio_is_not_downgraded(self):
        from unittest.mock import Mock
        port = Mock()
        with patch.object(updater.time, "sleep"), patch.object(updater, "command", side_effect=[
            b"OK\r\n", b"AT+VER=RUI_4.4.0_RAK3172-E\r\nOK\r\n"]):
            with self.assertRaisesRegex(ValueError, "downgrade"):
                updater.update(port, b"not sent", None)
        self.assertEqual(port.write.call_args_list[0].args, (b"\r\n",))
        self.assertEqual(port.write.call_count, 1)

    def test_bridge_preserves_binary_bytes_in_both_directions(self):
        runtime = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <vector>
constexpr int SERIAL_8N1=0;
struct HardwareSerial {
 std::vector<uint8_t> rx,tx;size_t pos=0,buffer=0;int baud=0,rx_pin=-1,tx_pin=-1;
 void setRxBufferSize(size_t n){buffer=n;}
 void begin(int b,int=0,int r=-1,int t=-1){baud=b;rx_pin=r;tx_pin=t;}
 int available(){return rx.size()-pos;}
 int read(){return rx[pos++];}
 void write(const uint8_t* p,size_t n){tx.insert(tx.end(),p,p+n);}
};
inline HardwareSerial Serial,Serial1;
inline void delay(int){}
'''
        test = r'''
#include <cassert>
#include "rak_bridge.cpp"
int main(){
 setup();assert(Serial.buffer==8192 && Serial1.buffer==8192);
 assert(Serial1.rx_pin==19 && Serial1.tx_pin==22 && Serial1.baud==115200);
 for(int i=0;i<8192;++i){Serial.rx.push_back(i%256);Serial1.rx.push_back(255-i%256);}
 for(int i=0;i<33;++i)loop();
 assert(Serial1.tx==Serial.rx && Serial.tx==Serial1.rx);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "Arduino.h").write_text(runtime)
            (path / "test.cpp").write_text(test)
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-DMODULINKR_RAK_BRIDGE", "-I", str(path), "-I", str(NODE / "src"),
                            str(path / "test.cpp"), "-o", str(path / "test")], check=True)
            subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
