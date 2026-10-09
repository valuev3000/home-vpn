import importlib.util
import json
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("home_vpn_config.py")
SPEC = importlib.util.spec_from_file_location("home_vpn_config", SCRIPT)
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class SharedConfigurationTests(unittest.TestCase):
    def test_version_has_one_source(self):
        self.assertEqual(CONFIG.PROJECT_VERSION, CONFIG.VERSION_FILE.read_text(encoding="utf-8").strip())

    def test_default_config_and_menu_validate(self):
        self.assertEqual([], CONFIG.validate_project_config(CONFIG.default_config()))
        self.assertEqual([], CONFIG.validate_menu(CONFIG.default_menu()))

    def test_schema_rejects_public_bot_admin_and_remote_internal_api(self):
        admin = {key: "value" for key in CONFIG.ENV_SCHEMAS["bot_admin"]["required"]}
        admin.update({"BOT_PANEL_BIND": "0.0.0.0", "BOT_PANEL_PORT": "8090"})
        self.assertTrue(any("loopback" in error for error in CONFIG.validate_env("bot_admin", admin)))
        bot = {key: "value" for key in CONFIG.ENV_SCHEMAS["bot"]["required"]}
        bot["SHOP_API_URL"] = "https://vpn.example.test"
        self.assertTrue(any("loopback" in error for error in CONFIG.validate_env("bot", bot)))

    def test_merge_preserves_existing_secret_on_empty_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "service.env"
            path.write_text("API_SECRET=keep-me\nPUBLIC_URL=https://old.example\n", encoding="utf-8")
            values = CONFIG.merge_env(path, {"API_SECRET": "", "PUBLIC_URL": "https://new.example"}, preserve_secrets={"API_SECRET"})
            self.assertEqual("keep-me", values["API_SECRET"])
            self.assertEqual("https://new.example", values["PUBLIC_URL"])
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_merge_does_not_replace_secret_without_explicit_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "service.env"
            path.write_text("API_SECRET=keep-me\n", encoding="utf-8")
            kept = CONFIG.merge_env(path, {"API_SECRET": "generated"}, preserve_secrets={"API_SECRET"})
            self.assertEqual("keep-me", kept["API_SECRET"])
            rotated = CONFIG.merge_env(
                path,
                {"API_SECRET": "rotated"},
                preserve_secrets={"API_SECRET"},
                overwrite_secrets=True,
            )
            self.assertEqual("rotated", rotated["API_SECRET"])

    def test_invalid_json_has_actionable_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.json"
            path.write_text('{"broken":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "строка 1, столбец"):
                CONFIG.load_json(path)

    def test_invalid_env_is_rejected_before_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bot.env"
            path.write_text("ORIGINAL=value\n", encoding="utf-8")
            values = {key: "value" for key in CONFIG.ENV_SCHEMAS["bot"]["required"]}
            values["SHOP_API_URL"] = "https://public.example"
            with self.assertRaisesRegex(ValueError, "loopback"):
                CONFIG.merge_env(path, values, component="bot")
            self.assertEqual("ORIGINAL=value\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
