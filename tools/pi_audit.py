"""Read-only Raspberry Pi inventory. Never collect credentials or camera images."""
import datetime
import hashlib
import json
import os
import pathlib
import platform
import subprocess

os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/sbin:/sbin"


def run(args, timeout=12):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"returncode": result.returncode, "stdout": result.stdout[-40000:], "stderr": result.stderr[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def read(path):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return None


commands = {
    "os": ["cat", "/etc/os-release"],
    "memory": ["free", "-b"],
    "storage": ["df", "-hT"],
    "usb": ["lsusb", "-t"],
    "services": ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"],
    "timers": ["systemctl", "list-timers", "--no-pager"],
    "failed": ["systemctl", "--failed", "--no-pager"],
    "processes": ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,ni,cls,psr", "--sort=-pcpu"],
    "sockets": ["ss", "-lntup"],
    "wifi": ["iw", "dev", "wlan0", "link"],
    "wifi_power": ["iw", "dev", "wlan0", "get", "power_save"],
    "wifi_station": ["iw", "dev", "wlan0", "station", "dump"],
    "qdisc": ["tc", "-s", "qdisc", "show", "dev", "wlan0"],
    "sysctl": ["sysctl", "net.core.default_qdisc", "net.ipv4.tcp_congestion_control", "net.ipv4.tcp_available_congestion_control", "vm.swappiness"],
    "clock": ["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"],
    "throttled": ["vcgencmd", "get_throttled"],
    "temperature": ["vcgencmd", "measure_temp"],
    "docker": ["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Status}}"],
    "docker_stats": ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
    "camera": ["v4l2-ctl", "-d", "/dev/video0", "--all"],
    "signaling_unit": ["systemctl", "cat", "pi-signaling"],
    "kernel_warnings": ["sudo", "-n", "journalctl", "-k", "-p", "warning", "-n", "60", "--no-pager"],
}
files = ["/proc/loadavg", "/proc/pressure/cpu", "/proc/pressure/memory", "/proc/pressure/io",
         "/proc/interrupts", "/proc/softirqs", "/sys/devices/system/cpu/cpufreq/policy0/scaling_governor",
         "/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq",
         "/sys/devices/system/cpu/cpufreq/policy0/scaling_available_governors",
         "/sys/class/thermal/thermal_zone0/temp", "/proc/sys/kernel/sched_rt_runtime_us"]
sources = ["/home/eur/project_0321plus/video-processor/src/main.cpp",
           "/home/eur/project_0321plus/webrtc-gateway/src/main.cpp",
           "/home/eur/signaling-server/server.js", "/home/eur/signaling-server/public/index.html",
           "/home/eur/signaling-server/latest-source.js", "/home/eur/signaling-server/latest-session.js"]
hashes = {p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sources if pathlib.Path(p).exists()}
print(json.dumps({"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "kernel": platform.release(),
                  "commands": {k: run(v) for k, v in commands.items()},
                  "files": {p: read(p) for p in files}, "sha256": hashes}, indent=2))
