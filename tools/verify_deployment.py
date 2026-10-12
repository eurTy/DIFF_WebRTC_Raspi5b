"""Read-only deployment proof. Prints hashes and selected policy, never images or secrets."""
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def command(args):
    return subprocess.check_output(args,text=True,timeout=20).strip()


root=Path(__file__).resolve().parent.parent
container='semantic-adaptive-v2-stream-1'
info=json.loads(command(['docker','inspect',container]))[0]
runtime=json.loads(command(['docker','exec',container,'python','-c',
    'import hashlib,json,pathlib;from semantic_video import __version__;r=pathlib.Path("/app");'
    'p=sorted(r.glob("semantic_video/*.py"))+sorted(r.glob("web/*.js"))+sorted(r.glob("web/*.css"))+sorted(r.glob("web/*.html"));'
    'print(json.dumps({"version":__version__,"files":{str(x.relative_to(r)):hashlib.sha256(x.read_bytes().replace(b"\\r\\n",b"\\n")).hexdigest() for x in p}}))']))
differences=[name for name,value in runtime['files'].items() if digest(root/name)!=value]
manifest=json.loads(Path('/home/eur/observer-unified-v1/integration/source-manifest.json').read_text())
legacy=[]
for item in manifest['files']:
    path=Path('/home/eur')/item['runtime']
    if not path.exists() or digest(path)!=item['sha256_lf']:
        legacy.append(item['runtime'])
host=info['HostConfig']
result={'normalization':'CRLF to LF','runtime':runtime,'runtime_differences':differences,
        'image_id':info['Image'],'user':info['Config']['User'],'read_only':host['ReadonlyRootfs'],
        'cap_drop':host['CapDrop'],'security_opt':host['SecurityOpt'],'nano_cpus':host['NanoCpus'],
        'memory_limit_bytes':host['Memory'],'restart_policy':host['RestartPolicy']['Name'],
        'input_mount_readonly':all(not m['RW'] for m in info['Mounts'] if m['Destination']=='/input'),
        'input_mount_present':any(m['Destination']=='/input' for m in info['Mounts']),
        'legacy_files_checked':len(manifest['files']),'legacy_differences':legacy,
        'temperature_c':int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,
        'throttled':command(['vcgencmd','get_throttled']),
        'wifi_power':command(['/usr/sbin/iw','dev','wlan0','get','power_save']),
        'old_containers':[{k:item[k] for k in ('Name','Image')}|{'running':item['State']['Running'],'started_at':item['State']['StartedAt']}
                          for item in json.loads(command(['docker','inspect','video-processor','webrtc-gateway']))]}
print(json.dumps(result,indent=2))
if differences or legacy or not result['input_mount_readonly'] or not result['input_mount_present']:
    raise SystemExit(1)
