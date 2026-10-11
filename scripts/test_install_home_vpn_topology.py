import importlib.util
import pathlib
import tarfile
import tempfile
import unittest
from unittest import mock


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
    def test_ssh_bootstrap_skips_password_when_key_already_works(self):
        completed=__import__('subprocess').CompletedProcess([],0,stdout="0\n")
        with mock.patch.object(MODULE,"run",return_value=completed) as runner:
            MODULE.ensure_ssh_key_access("root@node.example.test")
        self.assertEqual(1,runner.call_count);self.assertIn("BatchMode=yes",runner.call_args.args[0])

    def test_interactive_confirmation_repeats_until_exact_phrase(self):
        answers=iter(("wrong",MODULE.CONFIRMATION));original=getattr(MODULE,"input",None)
        MODULE.input=lambda _prompt:next(answers)
        try:self.assertIsNone(MODULE.require_confirmation())
        finally:
            if original is None:delattr(MODULE,"input")
            else:MODULE.input=original

    def test_local_control_node_wizard_keeps_foreign_nodes_remote(self):
        answers=iter((
            MODULE.DEFAULT_KIT_URL,MODULE.DEFAULT_KIT_SHA256,
            "10.10.0.10","1","DE","foreign-1","Estonia","root@foreign.example.test",
            "10.10.0.11","vpn.example.test","dl.google.com","","1","https://site.example.test","","n",
        ))
        with mock.patch.object(MODULE,"ask",side_effect=lambda *_args,**_kwargs:next(answers)):
            plan=MODULE.wizard(1,local_control_node=True)
        self.assertEqual("local",plan["site"]["ssh_host"])
        self.assertEqual("root@foreign.example.test",plan["nodes"][0]["ssh_host"])
        self.assertEqual(1,plan["mode"])

    def test_wizard_selects_foreign_vps_for_bot(self):
        answers=iter((
            MODULE.DEFAULT_KIT_URL,MODULE.DEFAULT_KIT_SHA256,"10.10.0.10","1",
            "PL","pl-1","Poland","root@198.51.100.20","198.51.100.20","10.10.0.10","10.10.0.10",
            "dl.google.com","","1","https://vpn.example.test","","y","1",
        ))
        with mock.patch.object(MODULE,"ask",side_effect=lambda *_args,**_kwargs:next(answers)):
            plan=MODULE.wizard(2,local_control_node=True)
        self.assertEqual("pl-1",plan["telegram_bot"]["placement"])
        self.assertEqual("pl-1",plan["emergency_node"])

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

    def test_telegram_bot_can_run_on_selected_foreign_node(self):
        value=topology(2);value["telegram_bot"]={"enabled":True,"placement":"foreign-1"}
        MODULE.validate_topology(value)
        self.assertEqual("root@node-1.example.test",MODULE.bot_target(value))
        self.assertIn("foreign-1",MODULE.bot_location_label(value))

    def test_remote_bot_rejects_unknown_node_and_public_admin_bind(self):
        value=topology(1);value["telegram_bot"]={"enabled":True,"placement":"missing"}
        with self.assertRaisesRegex(SystemExit,"placement"):MODULE.validate_topology(value)
        value=topology(1);value["telegram_bot"]={"enabled":True,"placement":"foreign-1"};value["nodes"][0]["address"]="8.8.8.8"
        with self.assertRaisesRegex(SystemExit,"приватным"):MODULE.validate_topology(value)

    def test_local_control_can_tunnel_bot_admin_to_public_foreign_vps(self):
        value=topology(2);value["site"]["ssh_host"]="local";value["nodes"][0]["address"]="203.0.113.11";value["telegram_bot"]={"enabled":True,"placement":"foreign-1"}
        MODULE.validate_topology(value)

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
    def test_fresh_control_site_gets_automatic_letsencrypt_certificate(self):
        value=topology(1);value["site"]["ssh_host"]="local"
        calls=[]
        with mock.patch.object(MODULE,"ssh_command",side_effect=lambda target,args,**kwargs:calls.append((target,args,kwargs))):
            MODULE.ensure_site_tls(value)
        self.assertEqual("/etc/letsencrypt/live/vpn.example.test/fullchain.pem",value["site"]["tls_cert"])
        self.assertEqual("/etc/letsencrypt/live/vpn.example.test/privkey.pem",value["site"]["tls_key"])
        self.assertEqual("local",calls[0][0])
        self.assertEqual(["bash","-s","--","vpn.example.test"],calls[0][1])
        self.assertIn("--register-unsafely-without-email",calls[0][2]["input_text"])
        self.assertIn("certbot.timer",calls[0][2]["input_text"])

    def test_automatic_letsencrypt_rejects_ip_address(self):
        value=topology(1);value["site"]["public_url"]="https://203.0.113.10"
        with self.assertRaisesRegex(RuntimeError,"домен"):
            MODULE.ensure_site_tls(value)

    def test_existing_site_certificate_skips_certbot(self):
        value=topology(1);value["site"].update({"tls_cert":"/etc/site.crt","tls_key":"/etc/site.key"})
        with mock.patch.object(MODULE,"ssh_command") as command:
            MODULE.ensure_site_tls(value)
        command.assert_not_called()

    def test_remote_bot_uses_https_site_api_and_private_panel_upstream(self):
        value=topology(2);value["telegram_bot"]={"enabled":True,"placement":"foreign-1"}
        credentials={"telegram_token":"123456789:"+("A"*35),"telegram_admin_id":"123456789","telegram_channel":"","bot_panel_user":"bot-admin","bot_panel_password":"StrongPassword123"}
        pushed=[];updated=[];scripts=[]
        details=type("Result",(),{"stdout":'{"bind":"10.10.0.11","entry":"/bot-secret"}'})()
        with mock.patch.object(MODULE,"site_secrets",return_value={"local_api":"http://127.0.0.1:8080","api":"secret","admin_path":"/admin"}), \
             mock.patch.object(MODULE,"remote_file_exists",return_value=False), \
             mock.patch.object(MODULE,"push_json",side_effect=lambda *args,**kwargs:pushed.append(args)), \
             mock.patch.object(MODULE,"ssh_script",side_effect=lambda *args,**kwargs:scripts.append(args)), \
             mock.patch.object(MODULE,"ssh_python",return_value=details), \
             mock.patch.object(MODULE,"update_remote_envs",side_effect=lambda *args,**kwargs:updated.append(args)):
            MODULE.install_bot(value,credentials)
        answers=next(payload for target,path,payload,*_rest in pushed if path.endswith("home-vpn-bot-answers.json"))
        self.assertEqual("https://vpn.example.test",answers["shop_api_url"])
        self.assertEqual("10.10.0.11",answers["bot_panel_bind"])
        site_update=next(changes for target,changes in updated if target=="root@central.example.test")
        self.assertEqual("http://10.10.0.11:8090",site_update["/etc/vpn-shop.env"]["BOT_ADMIN_UPSTREAM"])

    def test_local_control_uses_ssh_tunnel_for_remote_bot_admin(self):
        value=topology(2);value["site"]["ssh_host"]="local";value["telegram_bot"]={"enabled":True,"placement":"foreign-1"}
        commands=[]
        with mock.patch.object(MODULE,"ssh_script",side_effect=lambda target,script,**kwargs:commands.append((target,script))):
            upstream=MODULE.install_bot_admin_tunnel(value,"root@node-1.example.test")
        self.assertEqual("http://127.0.0.1:18090",upstream)
        encoded=commands[0][1].split("printf '%s' ",1)[1].split(" |",1)[0].strip("'")
        unit=__import__('base64').b64decode(encoded).decode()
        self.assertIn("127.0.0.1:18090:127.0.0.1:8090",unit)
        self.assertIn("root@node-1.example.test",unit)
    def test_role_update_covers_control_node_edges_and_backup_units(self):
        value=topology(1);commands=[];deployed=[];backups=[]
        with mock.patch.object(MODULE,"check_servers"), \
             mock.patch.object(MODULE,"build_source_archive"), \
             mock.patch.object(MODULE,"deploy_source",side_effect=lambda target,_archive:deployed.append(target)), \
             mock.patch.object(MODULE,"ssh_command",side_effect=lambda target,args,**_kwargs:commands.append((target,args))), \
             mock.patch.object(MODULE,"install_xui_backup",side_effect=lambda target:backups.append(target)), \
             mock.patch.object(MODULE,"ssh_script"), mock.patch.object(MODULE,"smoke_public_route"):
            MODULE.update_managed(value)
        self.assertEqual({"root@central.example.test","root@node-1.example.test"},set(deployed))
        self.assertEqual(["root@node-1.example.test"],backups)
        self.assertTrue(any(target=="root@central.example.test" and "site" in args for target,args in commands))
        self.assertTrue(any(target=="root@node-1.example.test" and "placeholder" in args for target,args in commands))

    def test_role_removal_cleans_cascade_and_preserves_xui(self):
        value=topology(2);commands=[];cascade=[]
        with mock.patch.object(MODULE,"check_servers"), \
             mock.patch.object(MODULE,"ssh_command",side_effect=lambda target,args,**kwargs:commands.append((target,args,kwargs))), \
             mock.patch.object(MODULE,"configure_cascade",side_effect=lambda plan,_inventories:cascade.append(plan)):
            MODULE.remove_managed(value,False)
        self.assertEqual(1,cascade[0]["mode"])
        flattened=[item for _target,args,_kwargs in commands for item in args]
        self.assertIn("placeholder",flattened);self.assertIn("site",flattened)
        self.assertNotIn("xui",flattened)

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
