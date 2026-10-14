"""Verifica el paquete de servicios y la barrera frente a imágenes sin recompilar."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'gateway/pi-web'))
script=ROOT/'shared/protocol/package_identity.py'
spec=importlib.util.spec_from_file_location('prepublic_package',script)
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_runtime_package_contains_code_without_images_or_node_credentials(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            output=Path(directory)/'runtime.tar.gz'
            package.package('gateway-runtime',output)
            with tarfile.open(output) as archive:
                names=archive.getnames()
            self.assertIn('gateway/pi-service/install_identity.sh',names)
            self.assertIn('gateway/pi-web/static/identity.js',names)
            self.assertFalse(any(Path(name).name.startswith(('nodo.bin','nodo-app.bin')) for name in names))
            self.assertFalse(any(name.endswith(('.env','.json','.db')) for name in names))

    def test_server_package_includes_current_health_parser(self):
        self.assertIn('server/consumer/health.py',package.files('server'))

    def test_installer_runtime_preflight_does_not_require_an_image(self):
        installer=(ROOT/'gateway/pi-service/install_identity.sh').read_text()
        code=installer.split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
        with patch.object(sys,'argv',['preflight',str(ROOT),'1']),contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code,'installer-preflight','exec'),{})

    def test_gateway_images_must_match_source_release(self):
        import firmwaremeta
        with patch.object(firmwaremeta,'image_version',return_value='0.0.1+010203040506'):
            with tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'full.tar.gz'
                with self.assertRaises(ValueError):package.package('gateway',output)
                self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
