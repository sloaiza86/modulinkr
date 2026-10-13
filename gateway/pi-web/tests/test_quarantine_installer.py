"""Comprueba instalación y recuperación con el recurso nuevo presente o ausente."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WEB = Path(__file__).resolve().parents[1]
FILES = ('dataapi.py', 'static/app.js', 'static/components.js', 'static/index.html',
         'static/style.css', 'static/quarantine.js')


class QuarantineInstallerTests(unittest.TestCase):
    def execute(self, fail=False, existing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, runtime, commands = (root / name for name in ('stage', 'runtime', 'commands'))
            stage.mkdir(); commands.mkdir()
            shutil.copyfile(WEB / 'install_quarantine.sh', stage / 'install.sh')
            for name in FILES:
                source, target = stage / 'gateway/pi-web' / name, runtime / name
                source.parent.mkdir(parents=True, exist_ok=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(WEB / name, source)
                if existing or name != 'static/quarantine.js': target.write_text('previous:' + name)
            (runtime / 'web_service.py').write_text('')
            (commands / 'systemctl').write_text('''#!/bin/bash
case "$1" in
show) echo "$ML_TEST_RUNTIME";;
stop|start) echo "$1" >> "$ML_TEST_LOG";;
is-active) test "${ML_TEST_FAIL:-0}" = 0;;
*) exit 11;;
esac
''')
            (commands / 'sudo').write_text('''#!/bin/bash
case "$1" in systemctl|install|rm) exec "$@";; *) exit 12;; esac
''')
            (commands / 'sleep').write_text('#!/bin/bash\nexit 0\n')
            for command in commands.iterdir(): command.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'],
                       ML_TEST_RUNTIME=str(runtime), ML_TEST_LOG=str(root / 'log'),
                       ML_TEST_FAIL=str(int(fail)), TMPDIR=str(root))
            result = subprocess.run(['bash', str(stage / 'install.sh')], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 1 if fail else 0, result.stderr)
            for name in FILES:
                if fail and not existing and name == 'static/quarantine.js':
                    self.assertFalse((runtime / name).exists())
                else:
                    expected = ('previous:' + name).encode() if fail else (WEB / name).read_bytes()
                    self.assertEqual((runtime / name).read_bytes(), expected, name)
            self.assertEqual((root / 'log').read_text().splitlines(),
                             ['stop', 'start', 'start'] if fail else ['stop', 'start'])

    def test_first_install(self): self.execute()
    def test_reinstall(self): self.execute(existing=True)
    def test_failure_removes_new_resource_and_restores_previous_files(self): self.execute(fail=True)
    def test_failure_restores_existing_resource(self): self.execute(fail=True, existing=True)


if __name__ == '__main__': unittest.main()
