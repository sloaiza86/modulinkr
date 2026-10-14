"""Los segmentos de escritura USB conservan NVS y las sesiones persistidas."""
from pathlib import Path
import struct
import sys
import unittest

SERVICE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVICE))
from firmware_images import flash_parts


class FirmwareNvsTests(unittest.TestCase):
    def test_distribution_segments_leave_nvs_untouched(self):
        image=(SERVICE/'nodo.bin').read_bytes()
        parts=flash_parts(image)
        self.assertEqual([at for at,_ in parts],[0,0xE000])
        flash=bytearray(b'\x55'*len(image))
        sentinel=bytes(flash[0x9000:0xE000])
        for at,data in parts: flash[at:at+len(data)]=data
        self.assertEqual(bytes(flash[0x9000:0xE000]),sentinel)
        self.assertEqual(bytes(flash[:0x9000]),image[:0x9000])
        self.assertEqual(bytes(flash[0xE000:]),image[0xE000:])

    def test_changed_nvs_partition_fails_before_writing(self):
        image=bytearray((SERVICE/'nodo.bin').read_bytes())
        struct.pack_into('<I',image,0x8004,0xA000)
        with self.assertRaises(ValueError): flash_parts(image)
        with self.assertRaises(ValueError): flash_parts(b'\xff'*100)


if __name__=='__main__': unittest.main()
