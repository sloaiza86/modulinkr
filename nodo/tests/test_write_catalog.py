"""Comprueba la configuración real y el catálogo binario que consume el gateway."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_write_commands import SRC,ARDUINO
sys.path.insert(0,str(SRC.parents[1]/'gateway/pi-service'))
import protocol

class CatalogTests(unittest.TestCase):
    def test_config_functions_and_maximum_catalog(self):
        main=(SRC/'main.cpp').read_text()
        catalog=main[main.index('size_t buildCatalog('):main.index('// ----- ts de captura')]
        writer=(SRC/'write_commands.cpp').read_text()
        signature=writer[writer.index('uint32_t signature('):writer.index('void begin()')]
        harness='''
#include <Arduino.h>
#include <ArduinoJson.h>
#include "write_commands.h"
#include <iostream>
cfg::Config g_cfg;
const char* firmwareIdentity(){return "0.0.79";}
namespace writecmd {
'''+signature+'}\n'+catalog+'''
int main(){std::string json;std::getline(std::cin,json);char error[256];
 if(!cfg::load(json.c_str(),g_cfg,error,sizeof(error))){std::cout<<error;return 2;}
 uint8_t bytes[2048];const auto n=buildCatalog(bytes,sizeof(bytes));if(!n)return 3;
 for(size_t i=0;i<n;++i)printf("%02x",bytes[i]);
}
'''
        config=dict(schema_version='3.3',node=dict(id=1,type='node',name='Estación'),transport=dict(
            lora=dict(region='EU868',frequency_hz=869525000,sf=7,bw_khz=125,tx_power_dbm=10,network_id=1,send_interval_ms=5000,ack_enabled=True,ack_timeout_ms=3000,max_retries=2),
            mesh=dict(relay_enabled=True,max_ttl=4,beacon_timeout_ms=90000,parent_min_rssi=-100,parent_hysteresis_db=6,parent_missed_frames=3,sn_offer_wait_ms=1000)),
            modbus=dict(baudrate=9600,parity='N',stopbits=1,devices=[]))
        for d in range(4):
            device=dict(name='módulo'+str(d),addressing=dict(default_slave_id=1,desired_slave_id=1),reads=[dict(id='temp',name='Temperatura',function='read_input_registers',address=0,type='int16'),dict(id='hum',name='Humedad',function='read_input_registers',address=1,type='uint16')],writes=[])
            for i,fn in enumerate(['write_single_coil','write_multiple_coils','write_single_register','write_multiple_registers']):
                entry=dict(id='action'+str(i),name='Acción'+str(i),function=fn,address=i*2,count=1)
                if i>=2:entry['type']='uint16'
                device['writes'].append(entry)
            config['modbus']['devices'].append(device)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Arduino.h').write_text(ARDUINO);(root/'test.cpp').write_text(harness)
            library=SRC.parent/'.pio/libdeps/atom-lite/ArduinoJson/src'
            subprocess.run(['clang++','-std=c++17','-Wno-deprecated-declarations','-DARDUINOJSON_ENABLE_ARDUINO_STRING=0','-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0','-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0','-I',tmp,'-I',str(SRC),'-I',str(library),str(root/'test.cpp'),str(SRC/'config.cpp'),'-o',str(root/'test')],check=True)
            def run():return subprocess.run([str(root/'test')],input=json.dumps(config)+'\n',text=True,capture_output=True)
            result=run();self.assertEqual(result.returncode,0,result.stdout)
            decoded=protocol.parse_catalog(bytes.fromhex(result.stdout));self.assertNotIn('error',decoded)
            self.assertTrue(decoded['write_capable']);self.assertEqual(len(decoded['writes']),16)
            self.assertEqual(decoded['writes'][-1]['device'],3)
            self.assertEqual(decoded['node_name'],'Estación')
            self.assertEqual(decoded['writes'][-1]['device_name'],'módulo3')
            self.assertEqual(decoded['writes'][-1]['name'],'Acción3')
            self.assertEqual(decoded['writes'][1]['function'],'write_multiple_coils')
            operation=config['modbus']['devices'][0]['writes'][2]
            operation['ui']=dict(kind='select',read_ids=['hum'],options=[dict(value=0,label='Automático'),dict(value=1,label='Manual')])
            result=run();self.assertEqual(result.returncode,0,result.stdout)
            decoded=protocol.parse_catalog(bytes.fromhex(result.stdout))
            self.assertEqual(decoded['writes'][2]['read_channels'],[1])
            self.assertEqual(decoded['writes'][2]['ui']['options'][1]['label'],'Manual')
            for ui in (dict(kind='select',options=[]),dict(kind='action',value=0.5),dict(read_ids=['unknown']),
                       dict(min=10,max=0),dict(step=0),dict(kind='switch'),dict(kind=5),
                       dict(kind='select',options=[dict(value=1,label='A'),dict(value=1,label='B')]),
                       dict(labels=['x']),dict(advanced='yes'),dict(note='x'*400)):
                operation['ui']=ui;self.assertEqual(run().returncode,2,ui)
            operation.pop('ui')
            device=config['modbus']['devices'][0]
            device['reads'][1].update(function='read_holding_registers',address=4)
            result=run();self.assertEqual(result.returncode,0,result.stdout)
            self.assertEqual(protocol.parse_catalog(bytes.fromhex(result.stdout))['writes'][2]['read_channels'],[1])
            device['reads'][1]['scale']=0.5
            result=run();self.assertEqual(result.returncode,0,result.stdout)
            self.assertEqual(protocol.parse_catalog(bytes.fromhex(result.stdout))['writes'][2]['read_channels'],[])
            entry=config['modbus']['devices'][0]['writes'][0]
            for fn in ('mask_write_register','read_write_multiple_registers'):
                entry['function']=fn;self.assertEqual(run().returncode,2)
            entry['function']='write_single_coil'
            for count in (0,126,257,True,"2"):
                entry['count']=count;self.assertEqual(run().returncode,2)
            entry['count']=1;entry['scale']=0;self.assertEqual(run().returncode,2)

if __name__=='__main__':unittest.main()
