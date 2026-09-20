#!/usr/bin/env python3
import importlib.util
import json
import os
import unittest

PATH=os.path.join(os.path.dirname(__file__),"home-vpn-xui.py")
SPEC=importlib.util.spec_from_file_location("home_vpn_xui",PATH);XUI=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(XUI)


class InboundPayloadTests(unittest.TestCase):
    def test_xhttp_payload_uses_tls_path_and_placeholder_fallback(self):
        payload,client,public=XUI.build_payload("xhttp","HOME",443,"vpn.example.test","/cert.pem","/key.pem","/private-path",8181,bootstrap=True)
        settings=json.loads(payload["settings"]);stream=json.loads(payload["streamSettings"])
        self.assertEqual(payload["protocol"],"vless");self.assertEqual(stream["network"],"xhttp");self.assertEqual(stream["security"],"tls")
        self.assertEqual(stream["xhttpSettings"]["path"],"/private-path");self.assertEqual(settings["fallbacks"][0]["dest"],8181)
        self.assertEqual(client["email"],"bootstrap-client");self.assertEqual(public["domain"],"vpn.example.test")

    def test_reality_payload_has_generated_key_and_short_id(self):
        payload,client,public=XUI.build_payload("reality","HOME",443,reality_target="www.cloudflare.com:443",bootstrap=True)
        stream=json.loads(payload["streamSettings"]);reality=stream["realitySettings"]
        self.assertEqual(stream["security"],"reality");self.assertTrue(reality["privateKey"]);self.assertEqual(len(reality["shortIds"][0]),16)
        self.assertTrue(public["public_key"]);self.assertEqual(client["flow"],"xtls-rprx-vision")


if __name__=="__main__":unittest.main()
