"""Pruebas de conexión con credenciales simuladas, sin servicios externos."""
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class Router:
    def __init__(self, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return lambda function: function

    post = get


class Response:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


class Request:
    def __init__(self, body):
        self.payload = body

    async def body(self):
        return json.dumps(self.payload).encode()


def load_api(name, dependencies):
    path = Path(__file__).resolve().parents[1] / (name + '.py')
    spec = importlib.util.spec_from_file_location('credential_test_' + name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        'fastapi': types.SimpleNamespace(APIRouter=Router, Request=Request),
        'fastapi.responses': types.SimpleNamespace(JSONResponse=Response),
        **dependencies,
    }):
        spec.loader.exec_module(module)
    return module


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.saved = secrets.token_urlsafe(24)
        self.api = load_api('mqttapi', {
            'netstatus': MagicMock(), 'settingsapi': MagicMock(),
        })
        self.client = MagicMock()
        self.client.loop_start.side_effect = lambda: self.client.on_connect(None, None, None, 0)
        self.mqtt = types.ModuleType('paho.mqtt.client')
        self.mqtt.Client = MagicMock(return_value=self.client)
        self.mqtt.CallbackAPIVersion = types.SimpleNamespace(VERSION1=1)
        self.mqtt.MQTTv311 = 4
        parent = types.ModuleType('paho.mqtt')
        parent.client = self.mqtt
        root = types.ModuleType('paho')
        root.mqtt = parent
        mock_modules = patch.dict(sys.modules, {
            'paho': root, 'paho.mqtt': parent, 'paho.mqtt.client': self.mqtt,
        })
        mock_modules.start()
        self.addCleanup(mock_modules.stop)
        self.reader_patch = patch.object(self.api.subprocess, 'run')
        self.reader = self.reader_patch.start()
        self.addCleanup(self.reader_patch.stop)
        self.reader.return_value = types.SimpleNamespace(
            returncode=0, stdout='MODULINKR_MQTT_PASS=' + self.saved + '\n', stderr='',
        )

    def probe(self, **overrides):
        body = dict(host='broker.example.test', user='test-user', password='', tls=False)
        body.update(overrides)
        return asyncio.run(self.api.probar(Request(body)))

    def test_blank_password_uses_current_credential_without_returning_it(self):
        result = self.probe()
        self.assertTrue(result['ok'])
        self.client.username_pw_set.assert_called_once_with('test-user', self.saved)
        self.assertNotIn(self.saved, json.dumps(result))
        self.reader.assert_called_once_with(
            ['sudo', '-n', str(self.api.GET_NET_SH)], capture_output=True, text=True, timeout=5,
        )
        self.api.settingsapi.set_section.assert_not_called()

    def test_new_password_is_used_only_for_probe(self):
        supplied = secrets.token_urlsafe(24)
        self.assertTrue(self.probe(password=supplied)['ok'])
        self.reader.assert_not_called()
        self.client.username_pw_set.assert_called_once_with('test-user', supplied)
        self.api.settingsapi.set_section.assert_not_called()

    def test_saved_password_is_read_again_after_rotation(self):
        self.probe()
        rotated = secrets.token_urlsafe(24)
        self.reader.return_value.stdout = 'MODULINKR_MQTT_PASS=' + rotated + '\n'
        self.probe()
        self.client.username_pw_set.assert_called_with('test-user', rotated)
        self.assertEqual(self.reader.call_count, 2)

    def test_password_spaces_and_equals_are_preserved(self):
        value = ' ' + self.saved + '== '
        self.reader.return_value.stdout = 'OTHER=value\nMODULINKR_MQTT_PASS=' + value + '\n'
        self.assertTrue(self.probe()['ok'])
        self.client.username_pw_set.assert_called_once_with('test-user', value)

    def test_reader_failure_does_not_connect_or_expose_output(self):
        self.reader.return_value.returncode = 1
        self.reader.return_value.stderr = self.saved
        result = self.probe()
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(self.saved, json.dumps(result.content))
        self.mqtt.Client.assert_not_called()

    def test_timeout_and_missing_field_fail_closed(self):
        for failure in (subprocess.TimeoutExpired('test', 5, output=self.saved), OSError(self.saved), None):
            with self.subTest(failure=type(failure).__name__):
                self.reader.side_effect = failure
                self.reader.return_value.stdout = 'OTHER=value\n'
                result = self.probe()
                self.assertEqual(result.status_code, 503)
                self.assertNotIn(self.saved, json.dumps(result.content))
        self.mqtt.Client.assert_not_called()

    def test_broker_rejection_does_not_expose_password(self):
        self.client.loop_start.side_effect = lambda: self.client.on_connect(None, None, None, 4)
        result = self.probe()
        self.assertEqual(result.status_code, 502)
        self.assertIn('contraseña incorrectos', result.content['error'])
        self.assertNotIn(self.saved, json.dumps(result.content))

    def test_mqtt_status_does_not_return_saved_credential(self):
        self.api.settingsapi.get_section.return_value = {'password_set': True}
        self.api.netstatus.gateway_link_state.return_value = {'mqtt_connected': True}
        result = self.api.estado()
        self.assertTrue(result['password_set'])
        self.assertNotIn('password', result['config'])
        self.reader.assert_not_called()

    def test_database_uses_saved_or_supplied_password_without_saving(self):
        data = types.SimpleNamespace(PG_PASS=self.saved, psycopg2=MagicMock())
        api = load_api('dbapi', {'dataapi': data})
        with patch.object(api, '_sudo_stdin') as save:
            for supplied in ('', secrets.token_urlsafe(24)):
                result = asyncio.run(api.probar(Request(dict(
                    host='db.example.test', db='test', user='test-user', password=supplied,
                ))))
                self.assertTrue(result['ok'])
                self.assertEqual(data.psycopg2.connect.call_args.kwargs['password'], supplied or self.saved)
                self.assertNotIn(self.saved, json.dumps(result))
                self.assertEqual(data.PG_PASS, self.saved)
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
