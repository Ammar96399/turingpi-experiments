"""Configuration regressions using stubs: no database, HTTP, or hardware access."""

import os
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SOURCE = Path(__file__).resolve().parents[1] / "roles/time_proxy/files/time_proxy.py"


class FakeApp:
    def get(self, *args, **kwargs):
        return lambda function: function

    post = get


class TimeProxyConfigurationTests(unittest.TestCase):
    def load_source(self, environment):
        fastapi = types.SimpleNamespace(FastAPI=FakeApp)
        influx = types.SimpleNamespace(
            InfluxDBClient=MagicMock(),
            Point=MagicMock(),
            WritePrecision=MagicMock(),
        )
        write_api = types.SimpleNamespace(SYNCHRONOUS=object())
        stubs = {
            "fastapi": fastapi,
            "influxdb_client": influx,
            "influxdb_client.client": types.ModuleType("influxdb_client.client"),
            "influxdb_client.client.write_api": write_api,
            "uvicorn": types.ModuleType("uvicorn"),
        }
        with (
            patch.dict(sys.modules, stubs),
            patch.dict(os.environ, environment, clear=True),
        ):
            namespace = runpy.run_path(str(SOURCE))
        return namespace, influx.InfluxDBClient

    def test_missing_or_blank_token_fails_before_client_creation(self):
        for token in (None, "", "   "):
            with self.subTest(token=token):
                environment = {} if token is None else {"INFLUXDB_TOKEN": token}
                with self.assertRaisesRegex(RuntimeError, "INFLUXDB_TOKEN must be set"):
                    self.load_source(environment)

    def test_loopback_and_local_database_defaults(self):
        namespace, client = self.load_source({"INFLUXDB_TOKEN": "unit-test-token"})
        self.assertEqual(namespace["BIND_HOST"], "127.0.0.1")
        client.assert_called_once_with(
            url="http://localhost:8086",
            token="unit-test-token",
            org="IRISA",
        )
        self.assertEqual(namespace["BUCKET"], "test")

    def test_specific_management_address_override(self):
        namespace, _ = self.load_source(
            {
                "INFLUXDB_TOKEN": "unit-test-token",
                "TIME_PROXY_HOST": "192.0.2.20",
            }
        )
        self.assertEqual(namespace["BIND_HOST"], "192.0.2.20")

    def test_explicit_environment_overrides(self):
        namespace, client = self.load_source(
            {
                "INFLUXDB_TOKEN": "unit-test-token",
                "INFLUXDB_URL": "http://database.example:8086",
                "INFLUXDB_ORG": "research",
                "INFLUXDB_BUCKET": "experiments",
                "TIME_PROXY_HOST": "0.0.0.0",
            }
        )
        client.assert_called_once_with(
            url="http://database.example:8086",
            token="unit-test-token",
            org="research",
        )
        self.assertEqual(namespace["BUCKET"], "experiments")
        self.assertEqual(namespace["BIND_HOST"], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
