import importlib.util
import json
import pathlib
import sqlite3
import tarfile
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("install-home-vpn-single-node.py")
SPEC = importlib.util.spec_from_file_location("single_node_installer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidationTests(unittest.TestCase):
    def test_lets_encrypt_does_not_request_email(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('ask("Email для Let\'s Encrypt")', source)
        self.assertNotIn("cert_email", source)
        self.assertIn("--register-unsafely-without-email", MODULE.PREPARE_SERVER)

    def test_country_accepts_iso_alpha2_and_alpha3(self):
        self.assertEqual("EE", MODULE.validate_country("ee"))
        self.assertEqual("EST", MODULE.validate_country("est"))
        for value in ("E", "ESTO", "E1"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                MODULE.validate_country(value)

    def test_country_question_repeats_after_invalid_input(self):
        answers = iter(("ESTO", "est"))
        original = MODULE.ask
        MODULE.ask = lambda _prompt, _default: next(answers)
        try:
            self.assertEqual("EST", MODULE.ask_country())
        finally:
            MODULE.ask = original

    def test_yes_no_question_repeats_after_invalid_input(self):
        answers=iter(("maybe","y"));original=getattr(MODULE,"input",None)
        MODULE.input=lambda _prompt:next(answers)
        try:self.assertTrue(MODULE.ask_yes_no("Продолжить?",False))
        finally:
            if original is None:delattr(MODULE,"input")
            else:MODULE.input=original

    def test_confirmation_repeats_and_accepts_exact_phrase(self):
        answers=iter(("неверно","INSTALL HOME VPN"));original=getattr(MODULE,"input",None)
        MODULE.input=lambda _prompt:next(answers)
        try:self.assertIsNone(MODULE.require_confirmation("Подтверждение","INSTALL HOME VPN"))
        finally:
            if original is None:delattr(MODULE,"input")
            else:MODULE.input=original

    def test_sha_question_repeats_after_invalid_input(self):
        answers=iter(("bad","a"*64));original=MODULE.ask
        MODULE.ask=lambda _prompt,_default:next(answers)
        try:self.assertEqual("a"*64,MODULE.ask_validated("SHA","",MODULE.validate_sha256))
        finally:MODULE.ask=original

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

    def test_existing_domains_are_discovered_without_prompts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            database = root / "x-ui.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE settings (key TEXT, value TEXT)")
            connection.executemany("INSERT INTO settings VALUES (?,?)", (
                ("webBasePath", "/secret-panel/"),
                ("webPort", "2053"),
                ("webCertFile", "/etc/letsencrypt/live/panel.example.com/fullchain.pem"),
            ))
            connection.commit(); connection.close()
            enabled = root / "sites-enabled"; enabled.mkdir()
            (enabled / "site").write_text(
                "server { server_name vpn.example.com; root /var/www/html; }",
                encoding="utf-8",
            )
            (enabled / "panel").write_text(
                "server { server_name panel.example.com; location /secret-panel/ { proxy_pass http://127.0.0.1:2053; } }",
                encoding="utf-8",
            )
            self.assertEqual(
                ("vpn.example.com", "panel.example.com"),
                MODULE.detect_existing_domains(database, root),
            )

    def test_domain_questions_exist_only_as_detection_fallback(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('panel_domain_override, "Укажите домен 3x-ui"', source)
        self.assertIn('site_domain_override, "Укажите домен сайта-заглушки"', source)
        self.assertIn("validate_or_ask",source)
        self.assertIn("Автоопределение доменов не удалось", source)

    def test_healthcheck_waits_for_slow_application_start(self):
        healthcheck=(SCRIPT.parent/"home-vpn-healthcheck.sh").read_text(encoding="utf-8")
        setup=(SCRIPT.parent/"home-vpn-setup.py").read_text(encoding="utf-8")
        self.assertIn('while [ "$attempt" -le 15 ]',healthcheck)
        self.assertIn("run('/usr/local/sbin/home-vpn-healthcheck')",setup)
        self.assertLess(
            setup.index("run('/usr/local/sbin/home-vpn-healthcheck')"),
            setup.index("run('systemctl','enable','--now','home-vpn-healthcheck.timer'"),
        )


class PlanTests(unittest.TestCase):
    def test_lifecycle_menu_supports_install_update_backup_restore_and_remove(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"1": "install"', source)
        self.assertIn('"2": "update"', source)
        self.assertIn('"3": "backup"', source)
        self.assertIn('"4": "restore"', source)
        self.assertIn('"5": "remove"', source)
        self.assertIn("RESTORE HOME VPN", source)
        self.assertIn("DELETE HOME-VPN", source)
        self.assertIn("3x-ui, её inbounds и пользовательские клиенты удаляться не будут", source)
        self.assertIn('"ls-files", "-z"', source)

    def test_installation_detection_accepts_partial_installation(self):
        original = MODULE.installation_markers
        MODULE.installation_markers = lambda:["служба сайта"]
        try:self.assertTrue(MODULE.home_vpn_installed())
        finally:MODULE.installation_markers = original

    def test_backup_rejects_unsafe_member_paths(self):
        import io
        with tempfile.TemporaryDirectory() as directory:
            archive=pathlib.Path(directory)/"unsafe.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                member=tarfile.TarInfo("../../etc/shadow");payload=b"bad";member.size=len(payload)
                bundle.addfile(member,io.BytesIO(payload))
            with tarfile.open(archive,"r:gz") as bundle, self.assertRaises(ValueError):
                list(MODULE.safe_backup_members(bundle))

    def test_backup_rejects_files_below_symlink(self):
        import io
        with tempfile.TemporaryDirectory() as directory:
            archive=pathlib.Path(directory)/"unsafe-link.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                link=tarfile.TarInfo("etc/nginx/escape");link.type=tarfile.SYMTYPE;link.linkname="/etc/nginx"
                bundle.addfile(link)
                member=tarfile.TarInfo("etc/nginx/escape/changed.conf");payload=b"bad";member.size=len(payload)
                bundle.addfile(member,io.BytesIO(payload))
            with tarfile.open(archive,"r:gz") as bundle, self.assertRaisesRegex(ValueError,"символической ссылки"):
                MODULE.safe_backup_members(bundle)

    def test_backup_accepts_packaged_nginx_module_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=pathlib.Path(directory)/"nginx-module-link.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                link=tarfile.TarInfo("./etc/nginx/modules-enabled/70-mod-stream-geoip2.conf")
                link.type=tarfile.SYMTYPE
                link.linkname="/usr/share/nginx/modules-available/mod-stream-geoip2.conf"
                bundle.addfile(link)
            with tarfile.open(archive,"r:gz") as bundle:
                members=MODULE.safe_backup_members(bundle)
            self.assertEqual(1,len(members))

    def test_backup_rejects_other_external_symlink_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=pathlib.Path(directory)/"unsafe-external-link.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                link=tarfile.TarInfo("./etc/nginx/modules-enabled/evil.conf")
                link.type=tarfile.SYMTYPE;link.linkname="/etc/shadow"
                bundle.addfile(link)
            with tarfile.open(archive,"r:gz") as bundle, self.assertRaisesRegex(ValueError,"Небезопасная ссылка"):
                MODULE.safe_backup_members(bundle)

    def test_full_backup_verifies_checksum_manifest_and_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);source=root/"source";(source/"var/lib/vpn-shop").mkdir(parents=True)
            database=source/"var/lib/vpn-shop/shop.db";connection=sqlite3.connect(database);connection.execute("CREATE TABLE test(value)");connection.commit();connection.close()
            (source/"manifest.json").write_text(json.dumps({"format":1,"type":"home-vpn-full"}),encoding="utf-8")
            archive=root/"home-vpn-full-test.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                bundle.add(source/"manifest.json",arcname="manifest.json");bundle.add(source/"var",arcname="var")
            digest=MODULE.file_sha256(archive);archive.with_name(archive.name+".sha256").write_text(f"{digest}  {archive.name}\n",encoding="ascii")
            MODULE.verify_full_backup(archive)

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
