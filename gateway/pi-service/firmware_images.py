"""Escritura USB que conserva la partición NVS del nodo."""
from pathlib import Path
import struct
import sys


def flash_parts(data):
    if len(data)<=0x10000: raise ValueError('La imagen completa del nodo está truncada.')
    nvs=[]
    for position in range(0x8000,0x9000,32):
        magic,kind,subtype,offset,size=struct.unpack_from('<HBBII',data,position)
        if magic!=0x50AA: break
        if kind==1 and subtype==2: nvs.append((offset,size))
    if nvs!=[(0x9000,0x5000)]:
        raise ValueError('La tabla de particiones no permite conservar la NVS del nodo con este instalador.')
    return [(0,data[:0x9000]),(0xE000,data[0xE000:])]


if __name__=='__main__':
    try:
        directory=Path(sys.argv[2])
        for index,(address,data) in enumerate(flash_parts(Path(sys.argv[1]).read_bytes())):
            path=directory/('image-'+str(index)+'.bin')
            path.write_bytes(data);path.chmod(0o600)
            print(hex(address));print(path)
    except (ValueError,OSError,IndexError) as error: raise SystemExit(str(error))
