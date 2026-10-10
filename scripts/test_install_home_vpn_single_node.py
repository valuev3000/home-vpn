import importlib.util
import json
import pathlib
import sqlite3
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

    def test_public_ip_comes_from_shared_domain_address(self):
        original = MODULE.resolved_ipv4
        MODULE.resolved_ipv4 = lambda domain: {
            "vpn.example.com": {"203.0.113.10", "203.0.113.11"},
            "panel.example.com": {"203.0.113.10"},
        }[domain]
        try:
            self.assertEqual(
                "203.0.113.10",
                MODULE.detect_public_ipv4("vpn.example.com", "panel.example.com"),
            )
        finally:
            MODULE.resolved_ipv4 = original

    def test_public_ip_rejects_unrelated_domain_addresses(self):
        original = MODULE.resolved_ipv4
        MODULE.resolved_ipv4 = lambda domain: {
            "vpn.example.com": {"203.0.113.10"},
            "panel.example.com": {"203.0.113.11"},
        }[domain]
        try:
            with self.assertRaisesRegex(SystemExit, "общую IPv4"):
                MODULE.detect_public_ipv4("vpn.example.com", "panel.example.com")
        finally:
            MODULE.resolved_ipv4 = original


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

    def test_preserve_mode_is_written_to_plan(self):
        plan=MODULE.build_topology(
            public_ip="203.0.113.10",site_domain="vpn.example.com",panel_domain="panel.example.com",
            country="DE",node_name="Primary",sni="dl.google.com",bot_enabled=False,
            installer_url=MODULE.DEFAULT_KIT_URL,installer_sha256=MODULE.DEFAULT_KIT_SHA256,
            xui_install_mode="preserve",
        )
        self.assertEqual("preserve",plan["xui_install_mode"])

    def test_server_setup_uses_requested_ssh_port_and_pinned_vpn_ports(self):
        script = MODULE.PREPARE_SERVER
        self.assertIn("ubuntu:22.04|ubuntu:24.04", script)
        self.assertIn("-checkend 604800", script)
        self.assertIn('[ "$xui_mode" = preserve ] || openssl x509', script)
        self.assertIn('ufw allow "${ssh_port}/tcp"', script)
        self.assertIn('if [ "$xui_mode" = fresh ]',script)
        for port in (8388, 8444, 51821, 51822):
            self.assertIn(str(port), script)
        self.assertNotIn("PasswordAuthentication no", script)

    def test_preserve_preflight_is_read_only_and_accepts_xhttp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            database = root / "x-ui.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE settings (key TEXT, value TEXT);
                CREATE TABLE inbounds (protocol TEXT, enable INTEGER, stream_settings TEXT);
                INSERT INTO settings VALUES ('webBasePath','hidden');
            """)
            connection.execute(
                "INSERT INTO inbounds VALUES (?,?,?)",
                ("vless", 1, json.dumps({"network": "xhttp"})),
            )
            connection.commit()
            connection.close()
            vhost = root / "sites-enabled" / "vpn.example.com"
            vhost.parent.mkdir(parents=True)
            vhost.write_text("server { server_name vpn.example.com; }\n", encoding="utf-8")
            before = database.read_bytes()
            result = MODULE.preserve_preflight(
                "vpn.example.com", True, nginx_root=root, database=database,
            )
            self.assertEqual(1, result["inbounds"])
            self.assertEqual(before, database.read_bytes())

    def test_preserve_preflight_rejects_existing_root_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            database = root / "x-ui.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE settings (key TEXT, value TEXT);
                CREATE TABLE inbounds (protocol TEXT, enable INTEGER, stream_settings TEXT);
                INSERT INTO settings VALUES ('webBasePath','hidden');
                INSERT INTO inbounds VALUES ('vless',1,'{"network":"xhttp"}');
            """)
            connection.commit()
            connection.close()
            vhost = root / "sites-enabled" / "vpn.example.com"
            vhost.parent.mkdir(parents=True)
            vhost.write_text(
                "server { server_name vpn.example.com; location / { return 200; } }\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "location /"):
                MODULE.preserve_preflight(
                    "vpn.example.com", False, nginx_root=root, database=database,
                )

    def test_preserve_preflight_accepts_one_included_root_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            database = root / "x-ui.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE settings (key TEXT, value TEXT);
                CREATE TABLE inbounds (protocol TEXT, enable INTEGER, stream_settings TEXT);
                INSERT INTO settings VALUES ('webBasePath','hidden');
                INSERT INTO inbounds VALUES ('vless',1,'{"network":"xhttp"}');
            """)
            connection.commit(); connection.close()
            included = root / "snippets" / "shared.conf"
            included.parent.mkdir(parents=True)
            included.write_text("location / { try_files $uri =404; }\n", encoding="utf-8")
            vhost = root / "sites-enabled" / "vpn.example.com"
            vhost.parent.mkdir(parents=True)
            vhost.write_text(
                f"server {{ server_name vpn.example.com; include {included}; }}\n",
                encoding="utf-8",
            )
            result = MODULE.preserve_preflight(
                "vpn.example.com", False, nginx_root=root, database=database,
            )
            self.assertEqual(str(vhost.resolve()), result["vhost"])


if __name__ == "__main__":
    unittest.main()
