"""Render the role's connection settings without invoking Ansible or a service."""

import importlib
import unittest
from pathlib import Path

ROLE = Path(__file__).resolve().parents[1] / "roles/time_proxy"


class TimeProxyRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.yaml = importlib.import_module("yaml")
            cls.jinja = importlib.import_module("jinja2")
        except ImportError as exc:
            raise unittest.SkipTest(
                "Install requirements-dev.txt for role-template tests"
            ) from exc

    def test_readiness_and_reset_use_the_configured_connection_address(self):
        defaults = self.yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        tasks = self.yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        reset = self.yaml.safe_load((ROLE / "tasks/reset.yml").read_text())
        readiness = next(
            task["ansible.builtin.wait_for"]
            for task in tasks
            if "ansible.builtin.wait_for" in task
        )
        environment = self.jinja.Environment(undefined=self.jinja.StrictUndefined)
        cases = (
            ("127.0.0.1", "127.0.0.1", "http://127.0.0.1:5000"),
            ("0.0.0.0", "127.0.0.1", "http://127.0.0.1:5000"),
            ("192.0.2.20", "192.0.2.20", "http://192.0.2.20:5000"),
            ("::", "::1", "http://[::1]:5000"),
            ("2001:db8::20", "2001:db8::20", "http://[2001:db8::20]:5000"),
        )
        for bind, expected_connect, expected_url in cases:
            with self.subTest(bind=bind):
                context = {"time_proxy_host": bind}
                context["time_proxy_connect_host"] = environment.from_string(
                    defaults["time_proxy_connect_host"]
                ).render(context)
                context["time_proxy_url"] = environment.from_string(
                    defaults["time_proxy_url"]
                ).render(context)
                self.assertEqual(context["time_proxy_connect_host"], expected_connect)
                self.assertEqual(context["time_proxy_url"], expected_url)
                self.assertEqual(
                    environment.from_string(readiness["host"]).render(context),
                    expected_connect,
                )
                self.assertEqual(
                    environment.from_string(
                        reset[0]["ansible.builtin.uri"]["url"]
                    ).render(context),
                    expected_url + "/reset",
                )


if __name__ == "__main__":
    unittest.main()
