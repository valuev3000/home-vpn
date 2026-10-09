import importlib.util
import pathlib
import tempfile
import unittest


SCRIPT=pathlib.Path(__file__).with_name("home_vpn_installer.py")
SPEC=importlib.util.spec_from_file_location("home_vpn_installer",SCRIPT)
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OwnershipManifestTests(unittest.TestCase):
    def test_owned_copy_normalizes_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);source=root/"source";target=root/"target"
            source.write_text("unit",encoding="utf-8");source.chmod(0o666)
            MODULE.copy_owned(source,target,0o644)
            self.assertEqual(0o644,target.stat().st_mode & 0o777)

    def test_site_purge_never_owns_xui_backup(self):
        paths=MODULE.removal_plan("site",True)["paths"]
        self.assertNotIn("/usr/local/sbin/home-vpn-kit-backup",paths)
        self.assertFalse(any("kit-backup" in path for path in paths))
        self.assertNotIn("/etc/home-vpn",paths)

    def test_bot_removal_owns_egress_but_not_site(self):
        plan=MODULE.removal_plan("bot",True)
        self.assertIn("home-vpn-bot-egress",plan["services"])
        self.assertIn("/etc/home-vpn/bot-egress.json",plan["paths"])
        self.assertNotIn("/etc/vpn-shop.env",plan["paths"])
        self.assertNotIn("/var/lib/vpn-shop",plan["paths"])

    def test_component_plans_do_not_overlap_data(self):
        manifests=MODULE.manifests();components=list(manifests)
        for index,left in enumerate(components):
            left_data=set(manifests[left].get("data") or [])
            for right in components[index+1:]:
                self.assertFalse(left_data & set(manifests[right].get("data") or []),(left,right))


if __name__=="__main__":unittest.main()
