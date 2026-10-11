import importlib.util
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("home-vpn-system-check.py")
SPEC = importlib.util.spec_from_file_location("home_vpn_system_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CertificateDiscoveryTests(unittest.TestCase):
    def test_lets_encrypt_lineages_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            live = pathlib.Path(directory)
            first = live / "vpn.example.test" / "fullchain.pem"
            second = live / "panel.example.test" / "fullchain.pem"
            first.parent.mkdir();second.parent.mkdir()
            first.write_text("certificate-one", encoding="utf-8")
            second.write_text("certificate-two", encoding="utf-8")
            found = MODULE.certificate_paths(live)
            self.assertIn(first, found)
            self.assertIn(second, found)

    def test_duplicate_certificate_copies_are_reported_once(self):
        with tempfile.TemporaryDirectory() as directory:
            live = pathlib.Path(directory)
            first = live / "vpn.example.test" / "fullchain.pem"
            second = live / "panel.example.test" / "fullchain.pem"
            first.parent.mkdir();second.parent.mkdir()
            first.write_text("same-certificate", encoding="utf-8")
            second.write_text("same-certificate", encoding="utf-8")
            found = MODULE.certificate_paths(live)
            self.assertEqual(1, len([path for path in found if path in (first, second)]))


if __name__ == "__main__":
    unittest.main()
