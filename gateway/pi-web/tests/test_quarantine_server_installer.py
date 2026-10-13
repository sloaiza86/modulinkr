"""Comprueba el instalador de permisos sin conectar a PostgreSQL operativo."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[3] / 'server'


class QuarantineServerInstallerTests(unittest.TestCase):
    def execute(self, ready=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, commands, etc = (root / name for name in ('stage', 'commands', 'etc'))
            commands.mkdir(); etc.mkdir()
            (stage / 'server/db/migrations').mkdir(parents=True)
            shutil.copyfile(SERVER / 'install_quarantine_delete.sh', stage / 'install.sh')
            shutil.copyfile(SERVER / 'db/apply_migrations.sh', stage / 'server/db/apply_migrations.sh')
            shutil.copyfile(SERVER / 'db/migrations/004_quarantine_delete.sql',
                            stage / 'server/db/migrations/004_quarantine_delete.sql')
            (etc / 'database.env').write_text('MODULINKR_DB_NAME=modulinkr_test\n')
            (commands / 'id').write_text('#!/bin/bash\necho 0\n')
            (commands / 'sudo').write_text('#!/bin/bash\ntest "$1" = -u && test "$2" = postgres || exit 10\nshift 2\nexec "$@"\n')
            (commands / 'sha256sum').write_text('#!/bin/bash\nprintf "%064d  %s\\n" 0 "$1"\n')
            (commands / 'psql').write_text('''#!/bin/bash
case "$*" in
*"-tAc"*) echo "$ML_TEST_READY";;
*"SELECT checksum"*) exit 0;;
*"-c"*) printf '%s\\n' "$*" >> "$ML_TEST_LOG";;
*) cat >> "$ML_TEST_LOG";;
esac
''')
            for command in commands.iterdir(): command.chmod(0o755)
            env = dict(os.environ, PATH=str(commands)+os.pathsep+os.environ['PATH'],
                       MODULINKR_ETC=str(etc), ML_TEST_LOG=str(root/'log'),
                       ML_TEST_READY='t' if ready else 'f')
            result = subprocess.run(['bash', str(stage/'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if ready else 1, result.stderr)
            if ready:
                log = (root/'log').read_text()
                self.assertIn('GRANT DELETE ON TABLE public.quarantine TO modulinkr_ro', log)
                self.assertNotIn('DELETE FROM', log)
                self.assertNotIn('GRANT DELETE ON ALL', log)
                self.assertIn('borrar_muestras', log)
                self.assertIn('borrar_valores', log)
            else:
                self.assertIn('no existen', result.stderr)
                self.assertFalse((root/'log').exists())

    def test_installs_only_quarantine_permission(self): self.execute()
    def test_missing_table_or_role_stops_before_migration(self): self.execute(ready=False)


if __name__ == '__main__': unittest.main()
