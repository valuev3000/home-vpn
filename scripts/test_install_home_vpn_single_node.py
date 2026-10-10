import importlib.util
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("install-home-vpn-single-node.py")
SPEC = importlib.util.spec_from_file_location("single_node_installer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidationTests(unittest.TestCase):
    def test_domains_are_normalized(self):
        self.assertEqual("vpn.example.com", MODULE.validate_domain("VPN.Example.COM.", "domain"))

    def test_domain_rejects_shell_and_url_syntax(self):
        for value in ("https://vpn.example.com", "vpn.example.com;id", "localhost", "-bad.example.com"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                MODULE.validate_domain(value, "domain")

    def test_console_defaults_to_standard_ssh_port(self):
        original = MODULE.os.environ.pop("SSH_CONNECTION", None)
        try:
            self.assertEqual("22", MODULE.detected_ssh_port())
        finally:
            if original is not None:
                MODULE.os.environ["SSH_CONNECTION"] = original

    def test_installer_requires_https(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_https_url("http://example.com/install.sh")


class PlanTests(unittest.TestCase):
    def test_reads_only_explicit_env_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "service.env"
            path.write_text("# comment\nUSER=admin\nPASSWORD='secret value'\n", encoding="utf-8")
            self.assertEqual(
                {"USER": "admin", "PASSWORD": "secret value"},
                MODULE.read_env(path),
            )

    def test_builds_secret_free_mode_three_plan(self):
        plan = MODULE.build_topology(
            public_ip="203.0.113.10",
            site_domain="vpn.example.com", panel_domain="panel.example.com",
            country="DE", node_name="Primary", sni="dl.google.com",
            bot_enabled=True, installer_url=MODULE.DEFAULT_KIT_URL,
            installer_sha256=MODULE.DEFAULT_KIT_SHA256,
        )
        self.assertEqual(3, plan["mode"])
        self.assertEqual("local", plan["site"]["ssh_host"])
        self.assertEqual(plan["site"]["ssh_host"], plan["nodes"][0]["ssh_host"])
        self.assertEqual("panel.example.com", plan["nodes"][0]["api_host"])
        self.assertEqual("https://vpn.example.com", plan["site"]["public_url"])
        serialized = __import__("json").dumps(plan).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("token", serialized.replace("telegram_bot", ""))

    def test_server_setup_uses_requested_ssh_port_and_pinned_vpn_ports(self):
        script = MODULE.PREPARE_SERVER
        self.assertIn("ubuntu:22.04|ubuntu:24.04", script)
        self.assertIn("-checkend 604800", script)
        self.assertIn('ufw allow "${ssh_port}/tcp"', script)
        for port in (8388, 8444, 51821, 51822):
            self.assertIn(str(port), script)
        self.assertNotIn("PasswordAuthentication no", script)


if __name__ == "__main__":
    unittest.main()
