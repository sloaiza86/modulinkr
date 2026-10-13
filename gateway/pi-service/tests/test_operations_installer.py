"""Comprueba actualización y recuperación del instalador sobre rutas simuladas."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SERVICE=Path(__file__).resolve().parents[1]
FILES=('pi-service/buffer.py','pi-service/protocol.py','pi-service/modbus_command.py',
       'pi-web/commandapi.py','pi-web/static/app.js','pi-web/static/components.js',
       'pi-web/static/index.html','pi-web/static/style.css')


class InstallerTests(unittest.TestCase):
    def execute(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stage=root/'stage';runtime=root/'runtime';tools=root/'tools'
            tools.mkdir();stage.mkdir()
            shutil.copyfile(SERVICE/'install_operations.sh',stage/'install.sh')
            for name in FILES:
                source=stage/'gateway'/name;target=runtime/name
                source.parent.mkdir(parents=True,exist_ok=True);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(SERVICE.parent/name,source);target.write_text('previous:'+name)
            (runtime/'pi-service/gateway_service.py').write_text('')
            (runtime/'pi-web/web_service.py').write_text('')
            (tools/'systemctl').write_text('''#!/bin/bash
case "$1" in
show) case "$2" in modulinkr-gateway.service) echo "$ML_TEST_RUNTIME/pi-service";; modulinkr-web.service) echo "$ML_TEST_RUNTIME/pi-web";; *) exit 10;; esac;;
stop|start) echo "$1" >> "$ML_TEST_LOG";;
is-active) if [ "${ML_TEST_FAIL:-0}" = 1 ]; then exit 1; fi;;
*) exit 11;;
esac
''')
            (tools/'sudo').write_text('''#!/bin/bash
case "$1" in systemctl|install|rm) exec "$@";; *) exit 12;; esac
''')
            for tool in tools.iterdir():tool.chmod(0o755)
            env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],ML_TEST_RUNTIME=str(runtime),
                     ML_TEST_LOG=str(root/'log'),ML_TEST_FAIL=str(int(fail)),TMPDIR=str(root))
            result=subprocess.run(['bash',str(stage/'install.sh')],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,1 if fail else 0,result.stderr)
            for name in FILES:
                target=runtime/name
                expected=('previous:'+name).encode() if fail else (stage/'gateway'/name).read_bytes()
                self.assertEqual(target.read_bytes(),expected,name)
            self.assertEqual((root/'log').read_text().splitlines(),['stop','start','start'] if fail else ['stop','start'])

    def test_update_uses_detected_paths(self):self.execute()
    def test_failed_restart_restores_previous_files(self):self.execute(fail=True)


if __name__=='__main__':unittest.main()
