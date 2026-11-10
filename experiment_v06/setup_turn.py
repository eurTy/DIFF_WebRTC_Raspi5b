"""Generate private deployment secrets on the Pi; never prints secret values."""
import argparse
import ipaddress
import os
from pathlib import Path
import secrets


def setup(root, host, gid):
    address=ipaddress.ip_address(host)
    if address not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('A private tailnet IPv4 address is required')
    private=root/'private'
    private.mkdir(exist_ok=True)
    env=root/'.env'
    if env.exists() or (private/'turnserver.conf').exists():
        raise FileExistsError('Refusing to rotate an existing secret implicitly')
    secret=secrets.token_urlsafe(32)
    env.write_text(f'SHM_READER_GID={gid}\nRTC_TAILNET_IP={host}\nRTC_TURN_SECRET={secret}\n',encoding='ascii')
    config=f'''listening-ip={host}
relay-ip={host}
listening-port=3478
min-port=50000
max-port=50031
realm=svt.private
fingerprint
use-auth-secret
static-auth-secret={secret}
denied-peer-ip=0.0.0.0-255.255.255.255
allowed-peer-ip={host}
no-multicast-peers
no-tcp-relay
no-tls
no-dtls
no-cli
no-software-attribute
stale-nonce=600
max-allocate-lifetime=3600
user-quota=4
total-quota=8
max-bps=600000
bps-capacity=1200000
relay-threads=1
log-file=stdout
simple-log
pidfile=/tmp/turnserver.pid
'''
    target=private/'turnserver.conf'
    target.write_text(config,encoding='ascii')
    os.chmod(env,0o600)
    os.chmod(target,0o600)
    print('Created .env and private/turnserver.conf; no credentials printed')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--ip',required=True)
    parser.add_argument('--gid',type=int,default=1000)
    args=parser.parse_args()
    setup(args.root,args.ip,args.gid)
