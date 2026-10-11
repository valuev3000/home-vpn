#!/usr/bin/env python3
"""Read-only integration smoke-test for an installed HOME-VPN topology."""

import argparse
import json
import pathlib
import shlex
import subprocess
import time
import urllib.request

SSH_PORTS={}


def run(arguments,input_text=None):
    return subprocess.run(arguments,input=input_text,text=True,capture_output=True,check=False,timeout=45)


def ssh(target,script):
    command=["bash","-s"] if target=="local" else ["ssh","-p",str(SSH_PORTS.get(target,22)),"--",target,"bash","-s"]
    result=run(command,script)
    if result.returncode:raise RuntimeError(f"{target}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def check_public(url):
    error = None
    for attempt in range(9):
        try:
            with urllib.request.urlopen(url.rstrip("/")+"/health",timeout=20) as response:
                payload=json.load(response)
            if response.status==200 and payload.get("ok") is True:return
        except Exception as caught:
            error = caught
        if attempt < 8:time.sleep(2)
    raise RuntimeError("public_url не вернул HOME-VPN /health") from error


def smoke(topology):
    SSH_PORTS.clear()
    for item in [topology.get("site") or {},*(topology.get("nodes") or []),topology.get("telegram_bot") or {}]:
        if item.get("ssh_host") and item["ssh_host"]!="local":SSH_PORTS[item["ssh_host"]]=int(item.get("ssh_port",22))
    mode=int(topology["mode"]);site=topology["site"];target=site["ssh_host"]
    check_public(site["public_url"])
    base=r'''set -Eeuo pipefail
systemctl is-active --quiet vpn-shop
ss -lntH | grep -Eq '127\.0\.0\.1:8080|\[::1\]:8080'
python3 - <<'PY'
import pathlib,urllib.error,urllib.parse,urllib.request
values={}
for line in pathlib.Path('/etc/vpn-shop.env').read_text(encoding='utf-8').splitlines():
 if line and not line.lstrip().startswith('#') and '=' in line:
  key,value=line.split('=',1);values[key]=value.strip().strip("'\"")
public=values['PUBLIC_URL'].rstrip('/');path=values['ADMIN_ENTRY_PATH']
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(NoRedirect)
def post(user,password,origin=None):
 data=urllib.parse.urlencode({'username':user,'password':password}).encode()
 headers={'Content-Type':'application/x-www-form-urlencoded'}
 if origin:headers['Origin']=origin
 request=urllib.request.Request(public+path,data=data,headers=headers)
 try:return opener.open(request,timeout=20).status
 except urllib.error.HTTPError as error:return error.code
assert post(values['ADMIN_USER'],'definitely-wrong',public)==401
assert post(values['ADMIN_USER'],values['ADMIN_PASSWORD'],'https://invalid.example')==403
assert post(values['ADMIN_USER'],values['ADMIN_PASSWORD'])==403
assert post(values['ADMIN_USER'],values['ADMIN_PASSWORD'],public) in (302,303)
PY
'''
    ssh(target,base)
    bot=(topology.get("telegram_bot") or {});placement=str(bot.get("placement") or "control")
    if bool(bot.get("enabled",True)):
        selected=({"ssh_host":bot["ssh_host"],"address":bot.get("address","127.0.0.1")} if placement=="bot-vps" else next((node for node in topology["nodes"] if node["id"]==placement),None))
        bot_target=selected["ssh_host"] if selected else target
        tunnelled=bool(selected and target=="local")
        panel_bind="127.0.0.1" if tunnelled or not selected else selected["address"]
        bot_check=r'''set -Eeuo pipefail
systemctl is-active --quiet vpn-bot vpn-bot-admin home-vpn-bot-egress
ss -lntH | grep -Fq __BOT_PANEL_LISTEN__
! ss -lntH | grep -Eq '0\.0\.0\.0:8090|\[::\]:8090'
python3 - <<'PY'
import base64,json,pathlib,urllib.error,urllib.request
cfg=json.loads(pathlib.Path('/etc/home-vpn/bot-egress.json').read_text())
assert cfg['inbounds'][0]['listen'] in ('127.0.0.1','::1')
assert cfg['routing']['balancers'][0]['fallbackTag']=='blocked'
assert cfg['outbounds'][-1]['protocol']=='blackhole'
assert cfg['routing']['rules'][0]['domain']==['full:api.telegram.org']
values={}
for line in pathlib.Path('/etc/vpn-bot.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  key,value=line.split('=',1);values[key]=value
assert values['SHOP_API_URL'].startswith(('http://127.0.0.1:','http://[::1]:','https://'))
assert values['TELEGRAM_PROXY_URL'].startswith('http://127.0.0.1:') or values['TELEGRAM_PROXY_URL'].startswith('http://[::1]:')
admin={}
for line in pathlib.Path('/etc/vpn-bot-admin.env').read_text().splitlines():
 if line and not line.lstrip().startswith('#') and '=' in line:
  key,value=line.split('=',1);admin[key]=value.strip().strip("'\"")
url=values['PUBLIC_BASE_URL'].rstrip('/')+admin['BOT_PANEL_ENTRY_PATH']
def status(user,password):
 token=base64.b64encode((user+':'+password).encode()).decode()
 try:return urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Basic '+token}),timeout=20).status
 except urllib.error.HTTPError as error:return error.code
assert status(admin['BOT_PANEL_USER'],'definitely-wrong')==401
assert status(admin['BOT_PANEL_USER'],admin['BOT_PANEL_PASSWORD'])==200
PY
curl -x http://127.0.0.1:__EGRESS_PORT__ -fsS --max-time 20 https://api.telegram.org/ >/dev/null
'''.replace("__BOT_PANEL_LISTEN__",shlex.quote(("["+panel_bind+"]" if ":" in panel_bind else panel_bind)+":8090")).replace("__EGRESS_PORT__",str(int((topology.get("telegram_egress") or {}).get("port",18080))))
        ssh(bot_target,bot_check)
        if tunnelled:
            tunnel_port=int(bot.get("admin_tunnel_port",18090))
            ssh(target,f"set -Eeuo pipefail\nsystemctl is-active --quiet home-vpn-bot-admin-tunnel.service\nss -lntH | grep -Fq {shlex.quote('127.0.0.1:'+str(tunnel_port))}\n")
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
        services="x-ui nginx home-vpn-kit-backup.timer"+(" kit-sub" if topology.get("xui_install_mode","fresh")!="preserve" else "")
        node_check="set -Eeuo pipefail\nsystemctl is-active --quiet "+services+"\n"
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
