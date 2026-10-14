"""Bounded, read-only path diagnostics. No images, credentials or network changes."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import platform
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit


def command(args, timeout):
    started = time.perf_counter()
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=timeout)
        return {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
                'elapsed_s': time.perf_counter()-started}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': str(error), 'elapsed_s': time.perf_counter()-started}


def probe(url, targets, count):
    parsed = urlsplit(url)
    address = ipaddress.ip_address(parsed.hostname)
    if parsed.scheme != 'http' or not (address.is_private or address.is_loopback) or parsed.username or parsed.password:
        raise ValueError('An explicit local HTTP address without credentials is required')
    targets = [str(ipaddress.ip_address(target)) for target in targets]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'complete': False,
              'url': url, 'platform': platform.system(), 'scope': 'Sequential read-only probes; not video latency or a synchronized path experiment',
              'proxy': 'Bypassed only inside this diagnostic process', 'timer': 'perf_counter', 'http': [], 'ping': {}}
    for _ in range(count):
        started = time.perf_counter()
        row = {'at': datetime.now(timezone.utc).isoformat()}
        try:
            with opener.open(url, timeout=2) as response:
                data = json.load(response)
                row.update(status=response.status, service={key: data.get(key) for key in ('version', 'source', 'uptime_s', 'sessions', 'events')})
        except (OSError, ValueError) as error:
            row['error'] = str(error)
        row['rtt_ms'] = (time.perf_counter()-started)*1000
        result['http'].append(row)
        time.sleep(.2)
    for target in targets:
        args = ['ping', '-n', str(count), '-w', '1000', target] if platform.system() == 'Windows' else ['ping', '-n', '-c', str(count), '-i', '.2', '-W', '1', target]
        result['ping'][target] = command(args, count*2+5)
    if platform.system() == 'Linux':
        for label, args in {'power': ['vcgencmd', 'get_throttled'], 'wifi_power': ['iw', 'dev', 'wlan0', 'get', 'power_save'],
                            'uptime': ['cat', '/proc/uptime'], 'boot_id': ['cat', '/proc/sys/kernel/random/boot_id']}.items():
            result[label] = command(args, 5)
    result.update(complete=True, finished_at=datetime.now(timezone.utc).isoformat())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--targets', nargs='+', required=True)
    parser.add_argument('--count', type=int, default=20, choices=range(1, 61))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = probe(args.url, args.targets, args.count)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
