#!/usr/bin/env python3
"""Read-only integration smoke-test for an installed HOME-VPN topology."""

import argparse
import json
import pathlib
import shlex
import subprocess
import urllib.request


def run(arguments,input_text=None):
    return subprocess.run(arguments,input=input_text,text=True,capture_output=True,check=False,timeout=45)


def ssh(target,script):
    result=run(["ssh",target,"bash","-s"],script)
    if result.returncode:raise RuntimeError(f"{target}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def check_public(url):
    with urllib.request.urlopen(url.rstrip("/")+"/health",timeout=20) as response:payload=json.load(response)
    if response.status!=200 or payload.get("ok") is not True:raise RuntimeError("public_url не вернул HOME-VPN /health")


def smoke(topology):
    mode=int(topology["mode"]);site=topology["site"];target=site["ssh_host"]
    check_public(site["public_url"])
    base=r'''set -Eeuo pipefail
systemctl is-active --quiet vpn-shop vpn-bot vpn-bot-admin home-vpn-bot-egress
ss -lntH | grep -Eq '127\.0\.0\.1:8080|\[::1\]:8080'
ss -lntH | grep -Eq '127\.0\.0\.1:8090|\[::1\]:8090'
! ss -lntH | grep -Eq '0\.0\.0\.0:8090|\[::\]:8090'
python3 - <<'PY'
import json,pathlib
cfg=json.loads(pathlib.Path('/etc/home-vpn/bot-egress.json').read_text())
assert cfg['inbounds'][0]['listen'] in ('127.0.0.1','::1')
assert cfg['routing']['balancers'][0]['fallbackTag']=='blocked'
assert cfg['outbounds'][-1]['protocol']=='blackhole'
assert cfg['routing']['rules'][0]['domain']==['full:api.telegram.org']
values={}
for line in pathlib.Path('/etc/vpn-bot.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  key,value=line.split('=',1);values[key]=value
assert values['SHOP_API_URL'].split('://',1)[-1].split(':',1)[0] in ('127.0.0.1','localhost','[::1]')
assert values['TELEGRAM_PROXY_URL'].startswith('http://127.0.0.1:') or values['TELEGRAM_PROXY_URL'].startswith('http://[::1]:')
PY
'''
    ssh(target,base)
    cascade=r'''set -Eeuo pipefail
systemctl is-active --quiet home-vpn-cascade.service
nft -c -f /etc/home-vpn/cascade.nft
nft list table ip home_vpn_cascade >/dev/null
test -f /etc/sysctl.d/90-home-vpn-cascade.conf
'''
    no_cascade=r'''set -Eeuo pipefail
! systemctl is-active --quiet home-vpn-cascade.service
! nft list table ip home_vpn_cascade >/dev/null 2>&1
test ! -e /etc/home-vpn/cascade.nft
test ! -e /etc/sysctl.d/90-home-vpn-cascade.conf
'''
    ssh(target,cascade if mode==2 else no_cascade)
    if mode==3:
        ssh(target,"set -Eeuo pipefail\n! systemctl is-active --quiet home-vpn-placeholder.service\ntest ! -e /etc/home-vpn/placeholder.html\n")
    for node in topology["nodes"]:
        node_check="set -Eeuo pipefail\nsystemctl is-active --quiet x-ui nginx kit-sub home-vpn-kit-backup.timer\n"
        if mode in (1,2):node_check+="systemctl is-active --quiet home-vpn-placeholder.service\n"
        ssh(node["ssh_host"],node_check)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--config",type=pathlib.Path,required=True);parser.add_argument("--plan-only",action="store_true");arguments=parser.parse_args()
    topology=json.loads(arguments.config.read_text(encoding="utf-8"));mode=int(topology.get("mode",0))
    if mode not in (1,2,3):raise SystemExit("mode должен быть 1, 2 или 3")
    if mode==3 and (len(topology.get("nodes") or [])!=1 or topology["site"]["ssh_host"]!=topology["nodes"][0]["ssh_host"]):raise SystemExit("mode 3 должен использовать одну общую VPS")
    if arguments.plan_only:print(f"smoke plan valid for mode {mode}");return
    smoke(topology);print(f"HOME-VPN mode {mode}: smoke-test пройден")


if __name__=="__main__":main()
