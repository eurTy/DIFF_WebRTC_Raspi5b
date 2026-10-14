"""Recover only the known Docker device-disappearance failure, never manual stops."""
import argparse
import json
from pathlib import Path
import subprocess

CONTAINER = 'video-processor'


def eligible(info, device_present):
    state = info.get('State', {})
    host = info.get('HostConfig', {})
    error = state.get('Error', '')
    return (info.get('Name') == '/' + CONTAINER and state.get('Status') == 'exited'
            and state.get('ExitCode') == 1 and not state.get('OOMKilled')
            and host.get('RestartPolicy', {}).get('Name') == 'always'
            and 'error gathering device information' in error
            and '"/dev/video0"' in error and 'no such file or directory' in error
            and any(d.get('PathOnHost') == '/dev/video0' for d in host.get('Devices', []))
            and device_present)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    info = json.loads(subprocess.check_output(['docker','inspect',CONTAINER], text=True, timeout=10))[0]
    allowed = eligible(info, Path('/dev/video0').is_char_device())
    if not args.apply:
        print(json.dumps({'eligible': allowed, 'action': 'check-only'}))
        return
    if not allowed:
        return
    path = Path(__file__).resolve().parent.parent / 'runtime' / 'capture-recovery.json'
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    episode = info['State']['FinishedAt']
    count = previous.get('attempts', 0) if previous.get('episode') == episode else 0
    if count >= 3:
        return
    # Persist before starting: a command timeout must not create unlimited retries.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'episode': episode, 'attempts': count + 1}), encoding='utf-8')
    result = subprocess.run(['docker','start',CONTAINER], capture_output=True, text=True, timeout=20)
    print(json.dumps({'event': 'camera-device-returned', 'container': CONTAINER, 'attempt': count+1, 'exit_code': result.returncode}))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
