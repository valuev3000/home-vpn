#!/usr/bin/env python3
import unittest

from migrate_existing_clients import identity, import_payload, legacy_url, same_identity


class MigrationSafetyTests(unittest.TestCase):
    def test_same_identity_accepts_matching_client(self):
        old = {"email": "familia-io", "uuid": "uuid-1", "subId": "sub-1"}
        new = {"email": "familia-io", "id": "uuid-1", "subId": "sub-1"}
        self.assertTrue(same_identity(old, new))

    def test_same_identity_rejects_uuid_or_subscription_change(self):
        old = {"email": "familia-io", "uuid": "uuid-1", "subId": "sub-1"}
        self.assertFalse(same_identity(old, {**old, "uuid": "uuid-2"}))
        self.assertFalse(same_identity(old, {**old, "subId": "sub-2"}))

    def test_payload_preserves_identifiers(self):
        client = {"email": "familia-io", "uuid": "uuid-1", "subId": "sub-1", "limitIp": 3, "enable": True}
        payload = import_payload(client, [1, 2, 3])
        self.assertEqual(payload["client"]["id"], "uuid-1")
        self.assertEqual(payload["client"]["subId"], "sub-1")
        self.assertEqual(payload["inboundIds"], [1, 2, 3])

    def test_payload_refuses_incomplete_identity(self):
        with self.assertRaises(RuntimeError):
            import_payload({"email": "familia-io", "subId": "sub-1"}, [1])

    def test_legacy_link_uses_existing_sub_id(self):
        node = {"subscription_url_template": "https://sub.example/sub/{subId}"}
        client = {"email": "familia-io", "uuid": "uuid-1", "subId": "old token"}
        self.assertEqual(legacy_url(node, client), "https://sub.example/sub/old%20token")
        self.assertEqual(identity(client)["uuid"], "uuid-1")


if __name__ == "__main__":
    unittest.main()
