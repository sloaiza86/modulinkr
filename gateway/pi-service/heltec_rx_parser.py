#!/usr/bin/env python3
"""Diagnóstico de tramas LoRa 4.0 recibidas por la radio Heltec."""
import json
import os
import re
import sys
import serial
import protocol

LINE_RE=re.compile(r'\[rx\].*?rssi=(?P<rssi>[-\d.]+)\s+snr=(?P<snr>[-\d.]+)\s+hex=(?P<hex>[0-9a-fA-F]+)')


def parse_frame(frame,key=None):
    return protocol.parse_frame(frame,key)


def main():
    port=sys.argv[1] if len(sys.argv)>1 else '/dev/ttyUSB0'
    baud=int(sys.argv[2]) if len(sys.argv)>2 else 115200
    key=None
    if os.environ.get('MODULINKR_SEC_ENABLED','0')=='1':
        value=os.environ.get('MODULINKR_SEC_KEY','')
        if len(value)!=32: raise SystemExit('Falta la clave de radio en el entorno operativo.')
        try: key=bytes.fromhex(value)
        except ValueError: raise SystemExit('La clave de radio del entorno no es hexadecimal.')
    print('Protocolo: 4.0 Puerto:',port,'Baud:',baud)
    with serial.Serial(port,baud,timeout=1) as connection:
        while True:
            line=connection.readline().decode(errors='replace')
            match=LINE_RE.search(line)
            if not match: continue
            try: parsed=parse_frame(bytes.fromhex(match['hex']),key)
            except ValueError: parsed={'error':'Trama hexadecimal incompleta.'}
            fields=('error','schema_str','frame_type_name','origin_id','dest_id','seq','device_uid','boot_id','catalog_tag','reads','st','ack_boot','ack_ts')
            result={name:parsed[name] for name in fields if name in parsed}
            result.update(rssi=float(match['rssi']),snr=float(match['snr']))
            print(json.dumps(result,ensure_ascii=False,allow_nan=True),flush=True)


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: pass
