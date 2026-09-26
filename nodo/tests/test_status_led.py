"""Comprueba los patrones y la caducidad de las confirmaciones sin hardware."""
from pathlib import Path
import subprocess
import tempfile
import unittest

class StatusLedTest(unittest.TestCase):
    def test_states(self):
        source = r'''
#include "status_led.h"
#include <cassert>
using namespace statusled;
int main() {
    Input s;
    assert(color(s, 0)==blue && color(s, 1200)==0);
    s.configured=false; assert(color(s,1200)==red);
    s.configured=true; s.updating=true;
    assert(color(s,0)==violet && color(s,350)==0);
    s.updating=false;
    const Path paths[]={Path::Lora,Path::Custody,Path::Cellular};
    const uint32_t colors[]={green,yellow,cyan};
    for(int i=0;i<3;++i) {
        s.path=paths[i]; s.confirmed=false;
        assert(color(s,100)==colors[i] && color(s,1200)==0);
        s.confirmed=true; assert(color(s,1200)==colors[i]);
    }
    s.synchronized=false; assert(color(s,100)==blue);
    s.synchronized=true; s.modbusFault=true;
    assert(color(s,0)==red && color(s,300)==red && color(s,200)==cyan);
    assert(color(s,500)==cyan);
    s.path=Path::None; s.exhausted=true;
    assert(color(s,0)==red && color(s,1200)==0);
    Evidence e; assert(!e.fresh(100,10000));
    e.ok(100); assert(e.fresh(90100,10000));
    assert(!e.fresh(90101,10000));
    e.ok(100); assert(e.fresh(600100,600000));
    e.lost(); assert(!e.fresh(101,600000));
    e.ok(0xfffffff0); assert(e.fresh(32,10000));
}
'''
        with tempfile.TemporaryDirectory() as d:
            cpp=Path(d)/'led.cpp'; cpp.write_text(source)
            exe=Path(d)/'led'
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(Path(__file__).resolve().parents[1]/'src'),str(cpp),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

if __name__=='__main__': unittest.main()
