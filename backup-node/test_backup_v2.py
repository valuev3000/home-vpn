#!/usr/bin/env python3
import hashlib,importlib.util,io,json,os,pathlib,tarfile,tempfile,unittest
from unittest import mock

HERE=pathlib.Path(__file__).resolve().parent
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
CONTROL=module("backup_control","home-vpn-backup-panel-control.py");RESTORE=module("backup_restore","home-vpn-full-restore.py")

class BackupV2Tests(unittest.TestCase):
    def test_archive_manifest_and_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=pathlib.Path(temporary);server=root/"node-1";server.mkdir();content=b"database-content";digest=hashlib.sha256(content).hexdigest()
            work=root/"work";work.mkdir();(work/"databases").mkdir();(work/"databases"/"shop.db").write_bytes(content)
            manifest={"format":"home-vpn-full-v2","server_id":"node-1","files":{"databases/shop.db":digest}}
            (work/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8");archive=server/"node-1-20260101-000000.tar.gz"
            with tarfile.open(archive,"w:gz") as bundle:
                bundle.add(work/"manifest.json",arcname="manifest.json");bundle.add(work/"databases",arcname="databases")
            previous=CONTROL.ROOT;CONTROL.ROOT=root
            try:
                self.assertEqual(CONTROL.manifest(archive)["format"],"home-vpn-full-v2")
                CONTROL.verify("node-1",archive.name)
            finally:CONTROL.ROOT=previous

    def test_restore_rejects_parent_traversal(self):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode="w") as archive:
            item=tarfile.TarInfo("../outside");item.size=1;archive.addfile(item,io.BytesIO(b"x"))
        data.seek(0)
        with tarfile.open(fileobj=data,mode="r") as archive:
            with self.assertRaises(RuntimeError):RESTORE.validate_members(archive)

    def test_remove_server_preserves_archives_in_detached_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=pathlib.Path(temporary)/"active";detached=pathlib.Path(temporary)/"removed";root.mkdir()
            server=root/"prod-ru";server.mkdir();(server/"copy.tar.gz").write_bytes(b"backup")
            data={"servers":[{"id":"prod-ru","name":"RU","enabled":True}]}
            previous_root,previous_detached=CONTROL.ROOT,CONTROL.DETACHED_ROOT
            CONTROL.ROOT,CONTROL.DETACHED_ROOT=root,detached
            try:
                with mock.patch.object(CONTROL,"load",return_value=data),mock.patch.object(CONTROL,"save") as saved,mock.patch.object(CONTROL,"rewrite_key"):
                    CONTROL.remove_server("prod-ru",False)
                    saved.assert_called_once()
                    self.assertFalse(server.exists())
                    moved=list(detached.glob("prod-ru-*"))
                    self.assertEqual(len(moved),1)
                    self.assertTrue((moved[0]/"copy.tar.gz").is_file())
            finally:CONTROL.ROOT,CONTROL.DETACHED_ROOT=previous_root,previous_detached

    def test_duplicate_server_id_is_rejected(self):
        payload=json.dumps({"id":"prod-ru","name":"RU","role":"site","public_key":"ssh-ed25519 YWJj"})
        with mock.patch.object(CONTROL,"load",return_value={"servers":[{"id":"prod-ru"}]}):
            with self.assertRaisesRegex(RuntimeError,"уже существует"):CONTROL.add(payload)

    def test_existing_directory_can_be_adopted_as_archive_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=pathlib.Path(temporary);(root/"primary").mkdir();previous=CONTROL.ROOT;CONTROL.ROOT=root
            try:
                with mock.patch.object(CONTROL,"load",return_value={"servers":[]}),mock.patch.object(CONTROL,"save") as saved:
                    CONTROL.adopt_archive(json.dumps({"id":"primary","name":"HOME-VPN-Test"}))
                    record=saved.call_args.args[0]["servers"][0]
                    self.assertEqual(record["name"],"HOME-VPN-Test")
                    self.assertEqual(record["profile_type"],"archive")
                    self.assertFalse(record["enabled"])
            finally:CONTROL.ROOT=previous

    def test_configuration_groups_multiple_servers(self):
        data={"servers":[{"id":"prod-ru"},{"id":"prod-fi"},{"id":"test-ru"}],"configurations":[]}
        payload=json.dumps({"id":"production","name":"HOME-VPN Production","server_ids":["prod-ru","prod-fi"],"replace_members":True})
        with mock.patch.object(CONTROL,"load",return_value=data),mock.patch.object(CONTROL,"save") as saved:
            CONTROL.upsert_configuration(payload);result=saved.call_args.args[0]
            self.assertEqual(result["configurations"][0]["name"],"HOME-VPN Production")
            self.assertEqual([x.get("configuration_id") for x in result["servers"]],["production","production",None])

if __name__=="__main__":unittest.main()
