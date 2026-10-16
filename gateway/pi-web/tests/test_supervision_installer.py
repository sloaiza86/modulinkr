"""Comprueba la espera de disponibilidad del visor sin servicios operativos."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

INSTALLER = Path(__file__).resolve().parents[2] / 'pi-service/install_supervision.sh'


class ReadinessTests(unittest.TestCase):
    installer = INSTALLER

    def setUp(self):
        script = self.installer.read_text()
        source = script.rsplit("sudo python3 - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        self.namespace = {'__name__': 'installer_test'}
        exec(source.split('\nif not ready():', 1)[0], self.namespace)

    def run_probe(self, pids, responses):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch('subprocess.check_output', side_effect=pids), \
                patch.object(Path, 'read_bytes', return_value=b'uvicorn\0--port\08443\0--ssl-certfile\0cert.pem\0'), \
                patch('urllib.request.urlopen', side_effect=responses or [response] * len(pids)) as request, \
                patch.object(self.namespace['time'], 'sleep'), \
                patch.object(self.namespace['time'], 'monotonic', side_effect=range(100)):
            self.namespace['urlopen'] = request
            return self.namespace['ready'](len(pids) + 1), request

    def test_three_responses_from_same_process_confirm_startup(self):
        ok, request = self.run_probe(['12'] * 3, None)
        self.assertTrue(ok)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.args[0], 'https://127.0.0.1:8443/login')

    def test_restarting_process_never_counts_as_stable(self):
        ok, _ = self.run_probe(['12', '13', '14', '15'], None)
        self.assertFalse(ok)

    def test_delayed_startup_waits_for_real_http_response(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        ok, request = self.run_probe(['12'] * 4, [OSError('refused')] + [response] * 3)
        self.assertTrue(ok)
        self.assertEqual(request.call_count, 4)

    def test_refused_connection_fails_readiness(self):
        ok, _ = self.run_probe(['12'] * 4, [OSError('refused')] * 4)
        self.assertFalse(ok)


class MeshReadinessTests(ReadinessTests):
    installer = INSTALLER.with_name('install_identity.sh')

    def test_failed_probe_reports_the_error_and_process(self):
        with patch('builtins.print') as output:
            self.run_probe(['12'] * 4, [OSError('refused')] * 4)
        messages = ' '.join(str(call) for call in output.call_args_list)
        self.assertIn('PID=12', messages)
        self.assertIn('refused', messages)


class InstallerRecoveryTests(unittest.TestCase):
    def execute(self, fail):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / 'stage/gateway'
            commands = root / 'commands'
            commands.mkdir()
            files = ('pi-service/buffer.py', 'pi-web/web_service.py', 'pi-web/systemapi.py',
                     'pi-web/supervision.py', 'pi-web/static/index.html', 'pi-web/static/style.css',
                     'pi-web/static/app.js', 'pi-web/static/supervision.js')
            for name in files:
                source = INSTALLER.parent.parent / name
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                live = root / 'runtime' / name
                live.parent.mkdir(parents=True, exist_ok=True)
                if name not in ('pi-web/systemapi.py', 'pi-web/supervision.py', 'pi-web/static/supervision.js'):
                    live.write_text('previous:' + name)
            (root / 'runtime/pi-service/gateway_service.py').write_text('')
            shutil.copyfile(INSTALLER, stage / 'pi-service/install_supervision.sh')
            (commands / 'systemctl').write_text('''#!/bin/bash
case "$1" in
show) case "$2" in
modulinkr-gateway.service) echo "$ML_ROOT/runtime/pi-service";;
modulinkr-web.service) echo "$ML_ROOT/runtime/pi-web";;
*) exit 10;; esac;;
start|stop) echo "$1" >> "$ML_ROOT/log";;
is-active) exit 0;;
*) exit 11;;
esac
''')
            (commands / 'sudo').write_text('''#!/usr/bin/env python3
import os, pathlib, shutil, subprocess, sys
args = sys.argv[1:]
if args[0] == 'python3':
    sys.stdin.read()
    with open(os.environ['ML_ROOT'] + '/log', 'a') as stream: stream.write('probe\\n')
    sys.exit(int(os.environ['ML_FAIL']))
if args[0] == 'install':
    shutil.copyfile(args[-2], args[-1])
    pathlib.Path(args[-1]).chmod(0o644)
elif args[0] == 'rm':
    pathlib.Path(args[-1]).unlink(missing_ok=True)
else:
    sys.exit(subprocess.call(args))
''')
            for command in commands.iterdir():
                command.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'],
                       ML_ROOT=str(root), ML_FAIL=str(int(fail)), TMPDIR=str(root))
            result = subprocess.run(['bash', str(stage / 'pi-service/install_supervision.sh')],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, int(fail), result.stderr)
            for name in files:
                live = root / 'runtime' / name
                if fail and name in ('pi-web/systemapi.py', 'pi-web/supervision.py', 'pi-web/static/supervision.js'):
                    self.assertFalse(live.exists(), name)
                else:
                    expected = ('previous:' + name).encode() if fail else (stage / name).read_bytes()
                    self.assertEqual(live.read_bytes(), expected, name)
            self.assertEqual((root / 'log').read_text().splitlines(),
                             ['stop', 'start', 'probe', 'stop', 'start'] if fail else ['stop', 'start', 'probe'])

    def test_success_keeps_updated_files_after_probe(self):
        self.execute(False)

    def test_failed_probe_stops_services_then_restores_and_removes_new_files(self):
        self.execute(True)


if __name__ == '__main__':
    unittest.main()
