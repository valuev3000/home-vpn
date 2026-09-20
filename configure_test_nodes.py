#!/usr/bin/env python3
import json, secrets, ssl, sys, urllib.request, uuid

CTX = ssl._create_unverified_context()

def api(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip('/') + path, data=data, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, context=CTX, timeout=20) as response:
        payload = json.loads(response.read().decode())
    if not payload.get('success'):
        raise RuntimeError(f'{path}: {payload.get("msg", payload)}')
    return payload.get('obj')

def reality(base, token):
    keys = api(base, token, 'GET', '/panel/api/server/getNewX25519Cert')
    return {
        'show': False, 'xver': 0, 'dest': 'www.microsoft.com:443',
        'serverNames': ['www.microsoft.com', 'microsoft.com'],
        'privateKey': keys['privateKey'], 'publicKey': keys['publicKey'],
        'minClientVer': '', 'maxClientVer': '', 'maxTimeDiff': 0,
        'shortIds': [secrets.token_hex(4), secrets.token_hex(8)],
        'settings': {'publicKey': keys['publicKey'], 'fingerprint': 'chrome',
                     'serverName': 'www.microsoft.com', 'spiderX': '/'}
    }

def inbound(base, token, label, port, network):
    stream = {'network': network, 'security': 'reality', 'realitySettings': reality(base, token)}
    if network == 'tcp':
        stream['tcpSettings'] = {'acceptProxyProtocol': False, 'header': {'type': 'none'}}
    else:
        stream['xhttpSettings'] = {'path': '/' + secrets.token_urlsafe(9), 'host': '',
                                   'mode': 'auto', 'noSSEHeader': False,
                                   'xPaddingBytes': '100-1000'}
    body = {
        'up': 0, 'down': 0, 'total': 0, 'remark': label, 'enable': True,
        'expiryTime': 0, 'listen': '', 'port': port, 'protocol': 'vless',
        'settings': {'clients': [], 'decryption': 'none', 'fallbacks': []},
        'streamSettings': stream,
        'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic'],
                     'metadataOnly': False, 'routeOnly': False}
    }
    return api(base, token, 'POST', '/panel/api/inbounds/add', body)

def client(base, token, email, uid, inbound_ids):
    body = {'client': {'email': email, 'id': uid, 'flow': '', 'totalGB': 50 * 1024**3,
                       'expiryTime': 0, 'tgId': 0, 'limitIp': 2, 'enable': True,
                       'comment': 'Multi-node test client'},
            'inboundIds': inbound_ids}
    return api(base, token, 'POST', '/panel/api/clients/add', body)

def main():
    if len(sys.argv) != 7:
        raise SystemExit('usage: script BASE TOKEN LABEL TCP_PORT XHTTP_PORT CLIENTS_JSON')
    base, token, label, tcp_port, xhttp_port, clients_json = sys.argv[1:]
    existing = api(base, token, 'GET', '/panel/api/inbounds/list')
    wanted = {f'{label}-REALITY-TCP': ('tcp', int(tcp_port)),
              f'{label}-REALITY-XHTTP': ('xhttp', int(xhttp_port))}
    ids = []
    for remark, (network, port) in wanted.items():
        found = next((x for x in existing if x.get('remark') == remark), None)
        obj = found or inbound(base, token, remark, port, network)
        ids.append(obj['id'])
    for item in json.loads(clients_json):
        try: client(base, token, item['email'], item['id'], ids)
        except RuntimeError as exc:
            if 'already exists' not in str(exc).lower(): raise
    final = api(base, token, 'GET', '/panel/api/inbounds/list')
    print(json.dumps({'label': label, 'inboundIds': ids,
                      'inbounds': [{'id': x['id'], 'remark': x['remark'], 'port': x['port'],
                                    'network': x['streamSettings']['network'],
                                    'security': x['streamSettings']['security'],
                                    'clients': len(x['settings'].get('clients', []))}
                                   for x in final if x['id'] in ids]}, ensure_ascii=False))

if __name__ == '__main__': main()
