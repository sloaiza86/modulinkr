"""El comisionamiento RAK por USB se ejecuta en el navegador."""
from pathlib import Path
import subprocess
import sys
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[2]/'pi-service')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
import configapi

class BrowserOnlyUsbTests(unittest.TestCase):
    def test_web_starts_without_service_directory_in_pythonpath(self):
        web = Path(__file__).resolve().parents[1]
        code = '''
import sys
sys.path.insert(0, sys.argv[1])
import web_service
from fastapi.testclient import TestClient
response = TestClient(web_service.app).get('/login')
assert response.status_code == 200, response.status_code
'''
        result = subprocess.run([sys.executable, '-B', '-I', '-c', code, str(web)],
                                cwd=web, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_gateway_node_usb_routes_are_absent(self):
        app=FastAPI();app.include_router(configapi.router)
        with TestClient(app) as client:
            for method,url in [('get','/puertos'),('get','/nodo'),('post','/detectar'),('post','/subir'),('post','/borrar'),('post','/flash'),('post','/rak/info'),('post','/rak/prepare'),('get','/rak/jobs/1')]:
                self.assertEqual(getattr(client,method)('/api/config'+url).status_code,404,url)

    def test_browser_retains_rak_preparation_before_configuration(self):
        source=(Path(__file__).resolve().parents[1]/'static/app.js').read_text()
        put=source[source.index('  async put(jsonText)'):source.index('  async del()')]
        self.assertLess(put.index('rakPrepararLocal'),put.index('CFG.PUT'))
        self.assertNotIn('rakPrepararGateway',source)
