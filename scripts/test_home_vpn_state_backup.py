import importlib.util
import json
import pathlib
import sqlite3
import tarfile
import tempfile
import unittest


SCRIPT=pathlib.Path(__file__).with_name("home-vpn-state-backup.py")
SPEC=importlib.util.spec_from_file_location("home_vpn_state_backup",SCRIPT)
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


class BackupVerificationTests(unittest.TestCase):
    def test_verified_restore_extracts_to_explicit_temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);source=root/"source";source.mkdir();(source/"manifest.json").write_text(json.dumps({"component":"site"}),encoding="utf-8")
            database=source/"rootfs/var/lib/vpn-shop/shop.db";database.parent.mkdir(parents=True);connection=sqlite3.connect(database);connection.execute("CREATE TABLE test(value)");connection.commit();connection.close()
            archive=root/"site.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                bundle.add(source/"manifest.json",arcname="manifest.json");bundle.add(source/"rootfs",arcname="rootfs")
            restore=root/"restore";MODULE.verify(archive,"site",restore)
            self.assertTrue((restore/"rootfs/var/lib/vpn-shop/shop.db").is_file())

    def test_secret_backup_requires_encryption_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,"age-recipient"):
                MODULE.create("bot",pathlib.Path(directory),include_secrets=True)


if __name__=="__main__":unittest.main()
