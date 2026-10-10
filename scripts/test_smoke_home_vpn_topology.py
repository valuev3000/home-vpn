import json
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT=pathlib.Path(__file__).with_name("smoke-home-vpn-topology.py")


class SmokePlanTests(unittest.TestCase):
    def plan(self,mode):
        node={"ssh_host":"root@node"};site={"ssh_host":"root@node" if mode==3 else "root@central","public_url":"https://vpn.example"}
        return {"mode":mode,"site":site,"nodes":[node]}

    def test_all_three_mode_plans(self):
        for mode in (1,2,3):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                path=pathlib.Path(directory)/"plan.json";path.write_text(json.dumps(self.plan(mode)),encoding="utf-8")
                result=subprocess.run(["python3",str(SCRIPT),"--config",str(path),"--plan-only"],text=True,capture_output=True)
                self.assertEqual(0,result.returncode,result.stderr);self.assertIn(f"mode {mode}",result.stdout)

    def test_mode3_rejects_split_roles(self):
        value=self.plan(3);value["site"]["ssh_host"]="root@other"
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/"plan.json";path.write_text(json.dumps(value),encoding="utf-8")
            result=subprocess.run(["python3",str(SCRIPT),"--config",str(path),"--plan-only"],text=True,capture_output=True)
        self.assertNotEqual(0,result.returncode)

    def test_local_transport_does_not_require_sshd(self):
        namespace={}
        exec(SCRIPT.read_text(encoding="utf-8").replace("if __name__==\"__main__\":main()", ""),namespace)
        self.assertEqual("local-ok",namespace["ssh"]("local","printf local-ok"))

    def test_public_check_retries_startup_window(self):
        self.assertIn("for attempt in range(9)", SCRIPT.read_text(encoding="utf-8"))


if __name__=="__main__":unittest.main()
