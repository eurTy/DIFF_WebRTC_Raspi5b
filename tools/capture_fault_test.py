"""Bounded capture interruption experiment. Run locally on the Pi, no network changes."""
import argparse
import datetime
import json
import signal
import subprocess
import time
import urllib.request


def fetch(path):
    with urllib.request.urlopen("http://127.0.0.1:8092/" + path, timeout=2) as response:
        return json.load(response)


def pause(value):
    subprocess.run(["docker", "pause" if value else "unpause", "video-processor"], check=True, capture_output=True)


def interrupt(*_):
    raise KeyboardInterrupt()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 1 <= args.runs <= 5:
        raise ValueError("runs must be in 1..5")
    signal.signal(signal.SIGTERM, interrupt)
    result = {"experiment": "CAPTURE_PROCESS_PAUSE", "source": "REAL_PROCESS_FAULT_INJECTION", "runs": []}
    paused = False
    try:
        for index in range(args.runs):
            initial = fetch("status")
            if initial["quality"] != "GOOD" or initial["sample"]["capture"]["quality"] != "GOOD":
                raise RuntimeError("Fresh capture baseline required")
            old_ids = {e["id"] for e in fetch("events")}
            record = {"index": index + 1, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "pause_request_ms": time.monotonic() * 1000, "polls": []}
            result["runs"].append(record)
            paused = True
            pause(True)
            record["pause_ack_ms"] = time.monotonic() * 1000
            until = time.monotonic() + 6
            while time.monotonic() < until:
                s = fetch("status")
                record["polls"].append({"at_ms": time.monotonic() * 1000, "quality": s["quality"],
                                        "capture": s["sample"]["capture"], "alarms": s["active_alarms"]})
                time.sleep(.1)
            record["resume_request_ms"] = time.monotonic() * 1000
            pause(False)
            paused = False
            record["resume_ack_ms"] = time.monotonic() * 1000
            time.sleep(8)
            new = [e for e in fetch("events") if e["id"] not in old_ids and e["code"] == "CAPTURE_STALE"]
            record["events"] = new
            active = next(e for e in new if e["phase"] == "ACTIVE")
            recovered = next(e for e in new if e["phase"] == "RECOVERED")
            record["alarm_detection_ms"] = active["mono_ms"] - record["pause_ack_ms"]
            record["recovery_detection_ms"] = recovered["mono_ms"] - record["resume_ack_ms"]
            record["observer_stayed_good"] = all(p["quality"] == "GOOD" for p in record["polls"])
            record["evidence"] = [fetch("events/" + str(e["id"])) for e in new]
            print(json.dumps({k: v for k, v in record.items() if k not in ("polls", "evidence", "events")}), flush=True)
    finally:
        try:
            if paused:
                pause(False)
        finally:
            with open(args.output, "w", encoding="utf-8") as output:
                json.dump(result, output, indent=2)


if __name__ == "__main__":
    main()
