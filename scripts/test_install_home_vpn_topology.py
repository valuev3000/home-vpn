import importlib.util
import pathlib
import tarfile
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("install-home-vpn-topology.py")
SPEC = importlib.util.spec_from_file_location("home_vpn_topology", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def topology(mode=1, nodes=1):
    result = {
        "mode": mode,
        "protocol_installer": {"url": "https://downloads.example.test/installer.sh", "sha256": "0" * 64},
        "telegram_egress": {"listen": "127.0.0.1", "port": 18080, "strategy": "roundRobin", "probe_interval": "30s"},
        "site": {"ssh_host": "root@central.example.test", "address": "10.10.0.10", "public_url": "https://vpn.example.test"},
        "nodes": [],
        "emergency_node": "foreign-1",
        "bot_admin_url": "https://bot.example.test",
    }
    for index in range(1, nodes + 1):
        result["nodes"].append({
            "id": f"foreign-{index}", "name": f"Node {index}", "country": "DE",
            "ssh_host": f"root@node-{index}.example.test", "address": f"10.10.0.{10 + index}",
            "public_host": f"node{index}.example.test", "sni": "dl.google.com",
            "ingress_address": f"10.10.1.{10 + index}" if mode == 2 else "",
        })
    if mode == 3:
        result["nodes"] = [result["nodes"][0]]
        result["site"]["ssh_host"] = result["nodes"][0]["ssh_host"]
        result["site"]["address"] = result["nodes"][0]["address"]
    return result


class TopologyValidationTests(unittest.TestCase):
    def test_integer_question_repeats_until_value_is_in_range(self):
        answers=iter(("4","2"));original=MODULE.ask
        MODULE.ask=lambda _prompt,_default,_secret=False:next(answers)
        try:self.assertEqual(2,MODULE.ask_int("Режим",1,1,3))
        finally:MODULE.ask=original

    def test_country_accepts_two_or_three_letters(self):
        for country in ("EE", "EST"):
            with self.subTest(country=country):
                value = topology(3)
                value["nodes"][0]["country"] = country
                MODULE.validate_topology(value)
        value = topology(3)
        value["nodes"][0]["country"] = "ESTO"
        with self.assertRaisesRegex(SystemExit, "две или три"):
            MODULE.validate_topology(value)

    def test_country_question_repeats_after_invalid_input(self):
        answers = iter(("1", "est"))
        original = MODULE.ask
        MODULE.ask = lambda _prompt, _default: next(answers)
        try:
            self.assertEqual("EST", MODULE.ask_country("Код страны"))
        finally:
            MODULE.ask = original

    def test_preserve_is_limited_to_single_server(self):
        value=topology(3);value["xui_install_mode"]="preserve";MODULE.validate_topology(value)
        value=topology(1);value["xui_install_mode"]="preserve"
        with self.assertRaisesRegex(SystemExit,"только для mode 3"):MODULE.validate_topology(value)

    def test_local_target_is_valid_for_single_server(self):
        value = topology(3)
        value["site"]["ssh_host"] = "local"
        value["nodes"][0]["ssh_host"] = "local"
        MODULE.validate_topology(value)

    def test_direct_allows_multiple_nodes(self):
        MODULE.validate_topology(topology(1, 2))

    def test_cascade_requires_unique_ingress_addresses(self):
        value = topology(2, 2)
        value["nodes"][1]["ingress_address"] = value["nodes"][0]["ingress_address"]
        with self.assertRaisesRegex(SystemExit, "уникальные входные IP"):
            MODULE.validate_topology(value)

    def test_single_server_requires_same_container(self):
        value = topology(3)
        value["site"]["ssh_host"] = "root@other.example.test"
        with self.assertRaisesRegex(SystemExit, "одной VPS"):
            MODULE.validate_topology(value)

    def test_public_host_must_not_include_scheme(self):
        value = topology()
        value["nodes"][0]["public_host"] = "https://node.example.test"
        with self.assertRaisesRegex(SystemExit, "без http"):
            MODULE.validate_topology(value)

    def test_ssh_target_cannot_inject_openssh_options(self):
        value = topology()
        value["site"]["ssh_host"] = "-oProxyCommand=touch-pwned"
        with self.assertRaisesRegex(SystemExit, "недопустимый SSH-адрес"):
            MODULE.validate_topology(value)

    def test_public_url_cannot_inject_nginx_directives(self):
        value = topology()
        value["site"]["public_url"] = "https://vpn.example.test;return 200"
        with self.assertRaisesRegex(SystemExit, "credentials, path, query"):
            MODULE.validate_topology(value)

    def test_api_host_must_be_a_dns_name(self):
        value = topology()
        value["nodes"][0]["api_host"] = "https://panel.example.test/path"
        with self.assertRaisesRegex(SystemExit, "api_host"):
            MODULE.validate_topology(value)

    def test_telegram_bot_can_be_disabled(self):
        value = topology()
        value["telegram_bot"] = {"enabled": False}
        MODULE.validate_topology(value)
        self.assertFalse(MODULE.bot_enabled(value))

    def test_credentials_repeat_invalid_secret_values(self):
        answers=iter((
            "home-admin","short","AdminPassword123",
            "bad-token","123456789:"+("A"*35),
            "letters","123456789","bad channel","",
            "bot-admin","short","BotPanelPassword123",
        ))
        original=MODULE.ask
        MODULE.ask=lambda _prompt,_default="",_secret=False:next(answers)
        try:result=MODULE.credentials_wizard({"telegram_bot":{"enabled":True}})
        finally:MODULE.ask=original
        self.assertEqual("123456789",result["telegram_admin_id"])
        self.assertEqual("BotPanelPassword123",result["bot_panel_password"])


class GeneratedConfigurationTests(unittest.TestCase):
    def test_api_token_parser_does_not_accept_other_output(self):
        self.assertEqual("secret-token",MODULE.parse_xui_api_token("notice\napiToken: secret-token\n"))
        with self.assertRaises(RuntimeError):MODULE.parse_xui_api_token("no token")

    def test_empty_legacy_access_url_is_safe(self):
        self.assertEqual({}, MODULE.parse_env(""))
        self.assertEqual([], MODULE.parse_env("").get("XUI_ACCESS_URL", "").split())

    def test_local_ssh_command_runs_without_openssh(self):
        result = MODULE.ssh_command("local", ["python3", "-c", "print(123)"], capture=True)
        self.assertEqual("123", result.stdout.strip())

    def test_placeholder_is_never_installed_on_primary_site(self):
        direct = topology(1, 2)
        self.assertEqual(2, len(MODULE.placeholder_nodes(direct)))
        self.assertNotIn(direct["site"]["ssh_host"], {node["ssh_host"] for node in MODULE.placeholder_nodes(direct)})
        self.assertEqual([], MODULE.placeholder_nodes(topology(3)))

    def test_xhttp_link_becomes_masked_xray_outbound(self):
        node = topology()["nodes"][0]
        link = "vless://11111111-1111-4111-8111-111111111111@node.example.test:443?type=xhttp&security=reality&path=%2Fhidden&sni=cdn.example.test&fp=chrome&pbk=public-key&sid=abcd#node"
        outbound = MODULE.xhttp_outbound(node, link)
        self.assertEqual("vless", outbound["protocol"])
        self.assertEqual("xhttp", outbound["streamSettings"]["network"])
        self.assertEqual("reality", outbound["streamSettings"]["security"])
        self.assertEqual("/hidden", outbound["streamSettings"]["xhttpSettings"]["path"])
        self.assertEqual("public-key", outbound["streamSettings"]["realitySettings"]["publicKey"])

    def test_bot_egress_is_balanced_and_telegram_only(self):
        outbounds = [
            {"tag": "foreign-one", "protocol": "vless"},
            {"tag": "foreign-two", "protocol": "vless"},
        ]
        config = MODULE.bot_egress_config(outbounds, {"strategy": "roundRobin", "probe_interval": "15s"})
        self.assertEqual("127.0.0.1", config["inbounds"][0]["listen"])
        self.assertEqual("leastPing", config["routing"]["balancers"][0]["strategy"]["type"])
        self.assertEqual("blocked", config["routing"]["balancers"][0]["fallbackTag"])
        self.assertEqual(["full:api.telegram.org"], config["routing"]["rules"][0]["domain"])
        self.assertEqual("blocked", config["routing"]["rules"][1]["outboundTag"])
        self.assertEqual("blackhole", config["outbounds"][-1]["protocol"])
        self.assertEqual("https://api.telegram.org", config["observatory"]["probeUrl"])

    def test_telegram_egress_must_be_loopback(self):
        value = topology()
        value["telegram_egress"]["listen"] = "0.0.0.0"
        with self.assertRaisesRegex(SystemExit, "loopback"):
            MODULE.validate_topology(value)

    def test_source_archive_excludes_local_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "source.tar.gz"
            MODULE.build_source_archive(archive)
            with tarfile.open(archive) as bundle:
                names = {name.removeprefix("./") for name in bundle.getnames()}
        self.assertNotIn("config/config.json", names)
        self.assertIn("config/config.example.json", names)

    def test_source_archive_excludes_arbitrary_untracked_file(self):
        secret = MODULE.ROOT / "credentials-untracked-test.json"
        secret.write_text('{"secret":"must-not-deploy"}', encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory() as directory:
                archive = pathlib.Path(directory) / "source.tar.gz"
                MODULE.build_source_archive(archive)
                with tarfile.open(archive) as bundle:names=set(bundle.getnames())
            self.assertNotIn("credentials-untracked-test.json", {name.removeprefix("./") for name in names})
        finally:secret.unlink(missing_ok=True)

    def test_each_node_has_a_distinct_token_variable(self):
        value = topology(1, 2)
        inventories = {}
        for index, node in enumerate(value["nodes"], 1):
            inventories[node["id"]] = {
                "panel_url": f"https://{node['address']}:7463/secret",
                "routes": [{"inbound_id": index, "enabled": True}],
            }
        config = MODULE.site_config(value["nodes"], inventories)
        self.assertEqual([True, False], [node["primary"] for node in config["nodes"]])
        self.assertEqual(2, len({node["token_env"] for node in config["nodes"]}))

    def test_existing_site_settings_are_preserved(self):
        value = topology()
        inventories = {"foreign-1": {"panel_url": "https://10.10.0.11:7463/secret", "routes": []}}
        base = {"branding": {"name": "My VPN"}, "pricing": {"currency": "EUR"}, "nodes": []}
        config = MODULE.site_config(value["nodes"], inventories, base)
        self.assertEqual("My VPN", config["branding"]["name"])
        self.assertEqual("EUR", config["pricing"]["currency"])
        self.assertEqual("foreign-1", config["nodes"][0]["id"])

    def test_cascade_ports_match_kit_transports(self):
        rows = [
            {"remark": "Shadowsocks", "port": 12001},
            {"remark": "Hysteria2", "port": 12002},
            {"remark": "TUIC", "port": 12003},
            {"remark": "AmneziaWG", "port": 12004},
            {"remark": "AmneziaWG-3.1", "port": 12005},
        ]
        tcp, udp = MODULE.cascade_ports(rows)
        self.assertEqual([443, 12001], tcp)
        self.assertEqual([12002, 12001, 12003, 12004, 12005], udp)

    def test_mode3_xhttp_fallback_routes_to_home_vpn_and_is_idempotent(self):
        inbound = {"id": 7, "protocol": "vless", "settings": '{"clients":[],"fallbacks":[{"dest":8181,"xver":0}]}'}
        first = MODULE.mode3_fallback_payload(inbound)
        second = MODULE.mode3_fallback_payload({**inbound, "settings": first["settings"]})
        settings = __import__("json").loads(second["settings"])
        self.assertEqual([{"dest": 8080, "xver": 0}], settings["fallbacks"])
        self.assertNotIn("id", second)

    def test_mode3_kit_nginx_routes_site_by_sni(self):
        site = topology(3)["site"]
        site.update({"tls_cert": "/etc/letsencrypt/live/vpn.example.test/fullchain.pem", "tls_key": "/etc/letsencrypt/live/vpn.example.test/privkey.pem"})
        config = MODULE.mode3_nginx_config(site)
        self.assertIn("server_name vpn.example.test;", config)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", config)
        self.assertIn("listen 127.0.0.1:10446 ssl http2 proxy_protocol;", config)

    def test_kit_route_replaces_only_the_main_catch_all(self):
        calls=[];original=MODULE.ssh_command
        MODULE.ssh_command=lambda target,args,**kwargs:calls.append((target,args,kwargs))
        try:MODULE.configure_kit_nginx_route(topology(3))
        finally:MODULE.ssh_command=original
        self.assertEqual("local" if topology(3)["site"]["ssh_host"]=="local" else "root@node-1.example.test",calls[0][0])
        self.assertIn("proxy_pass http://127.0.0.1:8080",calls[0][2]["input_text"])
        self.assertIn("proxy_intercept_errors off",calls[0][2]["input_text"])
        self.assertIn("pattern.search(text)",calls[0][1][2])

    def test_preserved_route_does_not_hide_application_errors(self):
        source=SCRIPT.read_text(encoding="utf-8")
        start=source.index("def configure_preserved_nginx_route")
        end=source.index("def configure_mode3_public_route")
        self.assertIn("proxy_intercept_errors off",source[start:end])

    def test_local_sni_uses_kit_domain_flag_without_shared_certificate(self):
        source=SCRIPT.read_text(encoding="utf-8")
        self.assertIn('flags.extend(("--domain",node["sni"]))',source)
        self.assertIn('node.get("sni_mode")!="local"',source)

    def test_tls_path_cannot_inject_nginx_directives(self):
        value = topology()
        value["site"].update({"tls_cert": "/tmp/cert.pem;return 200", "tls_key": "/tmp/key.pem"})
        with self.assertRaisesRegex(SystemExit, "безопасный абсолютный путь"):
            MODULE.validate_topology(value)

    def test_cascade_cleanup_is_project_scoped_and_restores_forwarding(self):
        script = MODULE.cascade_cleanup_script()
        self.assertIn("cascade-ip-forward.previous", script)
        self.assertIn("90-home-vpn-cascade.conf", script)
        self.assertIn("delete table ip home_vpn_cascade", script)
        self.assertNotIn("flush ruleset", script)

    def test_reapply_and_mode_transitions_use_expected_cascade_action(self):
        rows=[{"remark":name,"port":12000+index} for index,name in enumerate(("Shadowsocks","Hysteria2","TUIC","AmneziaWG","AmneziaWG-3.1"),1)]
        inventories={"foreign-1":{"rows":rows}};calls=[];original=MODULE.ssh_script
        try:
            MODULE.ssh_script=lambda target,script,**kwargs:calls.append((target,script))
            mode2=topology(2);MODULE.configure_cascade(mode2,inventories);first=calls[-1]
            MODULE.configure_cascade(mode2,inventories);second=calls[-1]
            self.assertEqual(first,second);self.assertIn("nft -c -f",first[1]);self.assertEqual("root@central.example.test",first[0])
            MODULE.configure_cascade(topology(1),inventories);self.assertIn("cascade-ip-forward.previous",calls[-1][1])
            MODULE.configure_cascade(topology(3),inventories);self.assertIn("90-home-vpn-cascade.conf",calls[-1][1])
        finally:MODULE.ssh_script=original


if __name__ == "__main__":
    unittest.main()
