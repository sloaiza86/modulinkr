from identity_fixture import CPP as IDENTITY_CPP
"""Comprueba catálogo, fragmentación, sobre seguro y reensamblado del registro."""
import ast
import json
import hashlib
import logging
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest

from test_write_commands import SRC, ARDUINO

SERVICE = SRC.parents[1] / 'gateway/pi-service'
sys.path.insert(0, str(SERVICE))
import protocol
from buffer import GatewayBuffer


def functions(source, signature):
    start = 0
    bodies = []
    while True:
        start = source.find(signature, start)
        if start < 0:
            return '\n'.join(bodies)
        end = source.index('\n}', start) + 2
        bodies.append(source[start:end])
        start = end


CCM = r'''
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <iostream>
struct mbedtls_ccm_context {};
inline void hex(const uint8_t* p,size_t n){for(size_t i=0;i<n;++i)printf("%02x",p[i]);}
inline int mbedtls_ccm_encrypt_and_tag(mbedtls_ccm_context*,size_t n,
 const uint8_t* nonce,size_t nonce_len,const uint8_t* aad,size_t aad_len,
 const uint8_t* input,uint8_t* output,uint8_t* tag,size_t tag_len){
 std::cout<<"CCM ";hex(nonce,nonce_len);std::cout<<" ";hex(aad,aad_len);
 std::cout<<" ";hex(input,n);std::cout<<std::endl;
 std::string encrypted;if(!(std::cin>>encrypted)||encrypted.size()!=2*(n+tag_len))return -1;
 for(size_t i=0;i<n+tag_len;++i){
  const auto value=uint8_t(std::stoul(encrypted.substr(2*i,2),nullptr,16));
  if(i<n)output[i]=value;else tag[i-n]=value;
 }
 return 0;
}
'''


HARNESS = r'''
#include <Arduino.h>
#include <ArduinoJson.h>
#include <algorithm>
#include <cassert>
#include <iostream>
#define private public
#include "lora.h"
#undef private
#include "config.h"
#include "write_commands.h"
using std::min;
uint32_t clock_ms=0;
uint32_t millis(){return clock_ms;}
uint32_t esp_random(){return 0;}
namespace nodeclock {bool synced(){return true;}uint32_t epochNow(){return 1791837005+clock_ms/1000;}}
namespace diag {void log(const char*,const char*,const char*,const char*,...) {}}
struct {bool hasParent(){return true;}uint8_t parentId(){return 255;}} mesh;
cfg::Config g_cfg;
LoraP2P lora;
bool g_registered=false,g_lora_ready=true;
size_t g_reg_catalog_len=0;
uint8_t g_reg_frag_total=1,g_reg_frag_next=0;
uint32_t g_reg_next_ms=0,g_reg_backoff_ms=5000;
std::vector<std::vector<uint8_t>> sent;
LoraP2P::Status LoraP2P::sendRaw(const uint8_t* p,size_t n){
 assert(n<=protocol::kOverhead+protocol::kMaxPayload);
 assert(crc16(p,n-2)==(uint16_t(p[n-2])|uint16_t(p[n-1])<<8));
 sent.emplace_back(p,p+n);std::cout<<"RAW ";hex(p,n);std::cout<<std::endl;
 return Status::OK;
}
'''


CASES = r'''
void sendRound(){
 g_reg_frag_next=0;g_reg_next_ms=clock_ms;g_reg_backoff_ms=kRegBackoffMinMs;
 configureFragments();assert(g_reg_frag_total>0);
 std::cout<<"CAT ";hex(g_reg_catalog,g_reg_catalog_len);std::cout<<std::endl;
 std::string tag;std::cin>>tag;assert(deviceid::parse(tag.c_str(),8,deviceid::currentStamp().catalog));
 const size_t before=sent.size();
 for(uint8_t i=0;i<g_reg_frag_total;++i){
  registrationTick(clock_ms);assert(sent.size()==before+i+1);
  registrationTick(clock_ms);assert(sent.size()==before+i+1);
  clock_ms=g_reg_next_ms;
 }
 assert(g_reg_frag_next==0);
}
int main(int argc,char** argv){
 assert(argc==2);std::string json;std::getline(std::cin,json);char error[256];
 if(!cfg::load(json.c_str(),g_cfg,error,sizeof(error))){std::cerr<<error;return 2;}
 deviceid::currentAddress()=g_cfg.node_id;memset(deviceid::currentStamp().uid,g_cfg.node_id,6);
 lora.initialized_=true;lora.sec_enabled_=g_cfg.security_enabled;lora.ccm_ready_=true;
 lora.node_id_=g_cfg.node_id;lora.network_id_=g_cfg.network_id;lora.ttl_=g_cfg.max_ttl;
 const std::string mode=argv[1];
 if(mode=="catalog"){
  g_reg_catalog_len=buildCatalog(g_reg_catalog,sizeof(g_reg_catalog));
  assert(g_reg_catalog_len>0);sendRound();
 }else if(mode=="sizes"){
  for(const size_t n:{size_t(1),size_t(219),size_t(220),size_t(429),size_t(2048)}){
   g_reg_catalog_len=n;for(size_t i=0;i<n;++i)g_reg_catalog[i]=uint8_t(i);
   sendRound();
  }
 }else if(mode=="limits"){
  uint8_t data[228]={};
  const uint8_t max=lora.sec_enabled_ ? 182 : 190;
  assert(lora.sendNodeRegister(255,0,1,data,max)==LoraP2P::Status::OK);
  const size_t count=sent.size();
  assert(lora.sendNodeRegister(255,0,1,data,max+1)==LoraP2P::Status::INVALID_ARGS);
  assert(lora.sendNodeRegister(255,0,1,nullptr,1)==LoraP2P::Status::INVALID_ARGS);
  assert(lora.sendNodeRegister(255,0,1,data,0)==LoraP2P::Status::INVALID_ARGS);
  assert(lora.sendNodeRegister(255,0,0,data,1)==LoraP2P::Status::INVALID_ARGS);
  assert(lora.sendNodeRegister(255,1,1,data,1)==LoraP2P::Status::INVALID_ARGS);
  assert(sent.size()==count);
  lora.initialized_=false;
  assert(lora.sendNodeRegister(255,0,1,data,1)==LoraP2P::Status::NOT_INITIALIZED);
 }else assert(false);
}
'''


class RegisterFragmentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESCCM
        except ImportError:
            raise unittest.SkipTest('Se requiere cryptography para el puente CCM nativo')
        cls.cipher = AESCCM(bytes(16), tag_length=4)
        main = (SRC / 'main.cpp').read_text()
        driver = (SRC / 'lora.cpp').read_text()
        writer = (SRC / 'write_commands.cpp').read_text()
        constants = main[main.index('constexpr uint32_t kRegBackoffMinMs'):main.index('// ----- Supervisión de la radio')]
        catalog = main[main.index('size_t buildCatalog('):main.index('// ----- ts de captura')]
        signature = writer[writer.index('uint32_t signature('):writer.index('void begin()')]
        count = main[main.index('    if (g_reg_catalog_len > 0) {'):main.index('    // El driver Modbus se configura antes del banner:')]
        version = re.search(r'MODULINKR_FIRMWARE_VERSION "([^"]+)"', main).group(1)
        bodies = '\n'.join(functions(driver, s) for s in (
            'LoraP2P::Status LoraP2P::sendNodeRegister(',
            'LoraP2P::Status LoraP2P::buildAndSend(',
            'uint32_t LoraP2P::ownSecTs(',
            'void LoraP2P::buildNonce(', 'void LoraP2P::buildAad(',
            'uint16_t LoraP2P::crc16(',
            'const char* LoraP2P::statusToString(',
        ))
        source = (HARNESS + constants + '\nuint8_t g_reg_catalog[kCatalogMax];\n'
                  + 'const char* firmwareIdentity(){return "' + version + '+0231e715bdb1";}\n'
                  + 'namespace writecmd {\n' + signature + '}\n' + catalog + bodies
                  + functions(main, 'void registrationTick(')
                  + '\nvoid configureFragments(){\n' + count + '\n}\n' + CASES)
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(cls.folder.name)
        arduino = ARDUINO.replace('inline uint32_t millis(){return clock_us/1000;}',
                                  'uint32_t millis();')
        (root / 'Arduino.h').write_text(arduino + '\nstruct HardwareSerial {};\n')
        (root / 'mbedtls').mkdir()
        (root / 'mbedtls/ccm.h').write_text(CCM)
        (root / 'test.cpp').write_text(IDENTITY_CPP+source)
        cls.binary = root / 'test'
        library = SRC.parent / '.pio/libdeps/atom-lite/ArduinoJson/src'
        subprocess.run(['clang++', '-std=c++17', '-Wno-deprecated-declarations',
                        '-DARDUINOJSON_ENABLE_ARDUINO_STRING=0',
                        '-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0',
                        '-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0',
                        '-I', str(root), '-I', str(SRC), '-I', str(library),
                        str(root / 'test.cpp'), str(SRC / 'config.cpp'),
                        '-o', str(cls.binary)], check=True)
        tree = ast.parse((SERVICE / 'gateway_service.py').read_text())
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_handle_register')
        timeout = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'REG_REASSEMBLY_TIMEOUT_S' for t in n.targets))
        namespace = dict(protocol=protocol, time=time, LOG=logging.getLogger(__name__),
                         REG_REASSEMBLY_TIMEOUT_S=ast.literal_eval(timeout))
        exec(compile(ast.Module(body=[handler], type_ignores=[]), '<gateway register>', 'exec'), namespace)
        cls.handle_register = staticmethod(namespace['_handle_register'])

    def run_native(self, profile, secure=True, mode='catalog'):
        config = json.loads((SRC.parent / 'configs' / profile).read_text())
        config['transport']['lora']['security'] = dict(enabled=secure, key='00' * 16)
        process = subprocess.Popen([str(self.binary), mode], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, bufsize=1)
        groups = []
        frames = []
        try:
            process.stdin.write(json.dumps(config) + '\n')
            process.stdin.flush()
            for line in process.stdout:
                fields = line.strip().split()
                if fields[0] == 'CCM':
                    nonce, aad, plain = map(bytes.fromhex, fields[1:])
                    process.stdin.write(self.cipher.encrypt(nonce, plain, aad).hex() + '\n')
                    process.stdin.flush()
                elif fields[0] == 'CAT':
                    frames = []
                    groups.append((bytes.fromhex(fields[1]), frames))
                    process.stdin.write(hashlib.sha256(groups[-1][0]).hexdigest()[:16]+"\n")
                    process.stdin.flush()
                elif fields[0] == 'RAW':
                    raw = bytes.fromhex(fields[1])
                    self.assertLessEqual(len(raw), 242)
                    parsed = protocol.parse_frame(raw, key=bytes(16) if secure else None)
                    self.assertNotIn('error', parsed)
                    self.assertEqual(parsed['frame_type'], protocol.FRAME_NODE_REGISTER)
                    frames.append(parsed)
                else:
                    self.fail(line)
            process.stdin.close()
            self.assertEqual(process.wait(timeout=10), 0, process.stderr.read())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        return groups

    def check_reassembly(self, profile, secure, expected_size=None):
        groups = self.run_native(profile, secure)
        self.assertEqual(len(groups), 1)
        catalog, frames = groups[0]
        if expected_size is not None: self.assertEqual(len(catalog), expected_size)
        self.assertGreater(len(catalog), 219)
        self.assertEqual([len(f['catalog_frag']) for f in frames], [min(182,len(catalog)-i) for i in range(0,len(catalog),182)])
        self.assertEqual(b''.join(f['catalog_frag'] for f in frames), catalog)
        with tempfile.TemporaryDirectory() as tmp:
            buffer = GatewayBuffer(str(Path(tmp) / 'buffer.db'))
            self.addCleanup(buffer.close)
            welcomes = []
            service = types.SimpleNamespace(reg_partial={}, buf=buffer, n_reg=0,
                                            send_welcome=lambda *args: welcomes.append(args))
            for frame in reversed(frames[1:]):
                self.handle_register(service, frame)
                self.assertEqual(welcomes, [])
            self.handle_register(service, frames[0])
            self.assertEqual(welcomes, [(frames[0]['origin_id'], frames[0]['hop_src'], protocol.ACK_OK)])
            self.assertEqual(service.n_reg, 1)
            stored = buffer.catalog_get(frames[0]['origin_id'])
            self.assertEqual(len(stored['writes']), 4)
            self.assertTrue(stored['write_capable'])
            if 'xymd02' in profile:
                self.assertEqual([a['read_channels'] for a in stored['writes']],[[2],[3],[2],[]])
                self.assertTrue(stored['writes'][2]['ui']['advanced'])
                self.assertTrue(stored['writes'][3]['ui']['advanced'])
            else:
                self.assertEqual(stored['writes'][1]['ui']['kind'],'select')
                self.assertEqual(stored['writes'][2]['ui']['kind'],'select')

    def test_environment_operation_catalog_with_security(self):
        self.check_reassembly('nodo1-escrituras-xymd02.json', True)

    def test_environment_catalog_without_security(self):
        self.check_reassembly('nodo1-escrituras-xymd02.json', False)

    def test_supernode_catalog_with_security(self):
        self.check_reassembly('nodo2-escrituras-wt901c485.json', True)

    def test_fragment_boundaries_and_full_capacity(self):
        for secure in (True, False):
            for catalog, frames in self.run_native('nodo1-escrituras-xymd02.json', secure, 'sizes'):
                self.assertEqual(len(frames), (len(catalog) + 181) // 182)
                self.assertEqual([f['frag_idx'] for f in frames], list(range(len(frames))))
                self.assertTrue(all(f['frag_total'] == len(frames) for f in frames))
                self.assertTrue(all(0 < len(f['catalog_frag']) <= 182 for f in frames))
                self.assertEqual(b''.join(f['catalog_frag'] for f in frames), catalog)

    def test_driver_limits_with_security(self):
        self.run_native('nodo1-escrituras-xymd02.json', True, 'limits')

    def test_driver_limits_without_security(self):
        self.run_native('nodo1-escrituras-xymd02.json', False, 'limits')


if __name__ == '__main__':
    unittest.main()
