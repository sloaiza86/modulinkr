"""Comprueba actualización y recuperación del visor sobre rutas simuladas."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WEB = Path(__file__).resolve().parents[1]
FILES = ('dataapi.py', 'static/app.js', 'static/components.js',
         'static/index.html', 'static/style.css')


class HistoryInstallerTests(unittest.TestCase):
    def execute(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, runtime, commands = (root / name for name in ('stage', 'runtime', 'commands'))
            stage.mkdir()
            commands.mkdir()
            shutil.copyfile(WEB / 'install_history.sh', stage / 'install.sh')
            for name in FILES:
                source = stage / 'gateway/pi-web' / name
                target = runtime / name
                source.parent.mkdir(parents=True, exist_ok=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(WEB / name, source)
                target.write_text('previous:' + name)
            (runtime / 'web_service.py').write_text('')
            (commands / 'systemctl').write_text('''#!/bin/bash
test "$2" = modulinkr-web.service || test "$2" = --quiet || exit 10
case "$1" in
show) echo "$ML_TEST_RUNTIME";;
stop|start) echo "$1" >> "$ML_TEST_LOG";;
is-active) if [ "${ML_TEST_FAIL:-0}" = 1 ]; then exit 1; fi;;
*) exit 11;;
esac
''')
            (commands / 'sudo').write_text('''#!/bin/bash
case "$1" in systemctl|install) exec "$@";; *) exit 12;; esac
''')
            for command in commands.iterdir():
                command.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'],
                       ML_TEST_RUNTIME=str(runtime), ML_TEST_LOG=str(root / 'log'),
                       ML_TEST_FAIL=str(int(fail)), TMPDIR=str(root))
            result = subprocess.run(['bash', str(stage / 'install.sh')], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 1 if fail else 0, result.stderr)
            for name in FILES:
                expected = ('previous:' + name).encode() if fail else (WEB / name).read_bytes()
                self.assertEqual((runtime / name).read_bytes(), expected, name)
            self.assertEqual((root / 'log').read_text().splitlines(),
                             ['stop', 'start', 'start'] if fail else ['stop', 'start'])

    def test_update_uses_service_path(self):
        self.execute()

    def test_failed_restart_restores_files(self):
        self.execute(fail=True)
