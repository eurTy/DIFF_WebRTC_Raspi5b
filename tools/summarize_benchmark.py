"""Recompute each recorded frame/age statistic; fail if a saved summary is inconsistent."""
import argparse
import gzip
import json
import math
from pathlib import Path


def stats(values):
    a = sorted(x for x in values if isinstance(x, (float, int)) and math.isfinite(x))
    if not a:
        return {"n": 0}
    return {"n": len(a), "mean": sum(a)/len(a), "min": a[0], "p50": a[math.ceil(.5*len(a))-1],
            "p95": a[math.ceil(.95*len(a))-1], "p99": a[math.ceil(.99*len(a))-1], "max": a[-1]}


def recompute(data):
    raw = data["raw"]
    frames = raw["frames"]
    calibrated = [f for f in frames if f.get("offset") is not None and f.get("published") is not None]
    driver = [f for f in calibrated if f.get("timing") and f["timing"]["flags"] & 0xe000 == 0x2000
              and 0 < f["timing"]["driver"] <= f["timing"]["dequeued"]]
    intervals = [b["decoded"]-a["decoded"] for a, b in zip(frames, frames[1:])]
    latency = [f["decoded"]+f["offset"]-f["published"] for f in calibrated]
    duration = (raw["end"]-raw["start"])/1000
    calculated = {"duration": duration, "frameCount": len(frames), "calibratedCount": len(calibrated),
                  "fps": len(frames)/duration, "latency": stats(latency),
                  "driverToDecode": stats(f["decoded"]+f["offset"]-f["timing"]["driver"] for f in driver),
                  "age": stats(a.get("age") for a in raw["ages"]), "interval": stats(intervals),
                  "negativeLatency": sum(x < 0 for x in latency),
                  "gaps200": sum(x > 200 for x in intervals), "gaps1000": sum(x > 1000 for x in intervals),
                  "statusBad": sum(o.get("state", {}).get("quality") != "GOOD" for o in data["observations"])}
    def compare(expected, actual, path=""):
        if isinstance(expected, dict):
            for key, value in expected.items(): compare(value, actual[key], path+"."+key)
        elif not math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"Summary mismatch {path}: {expected} != {actual}")
    compare(calculated, data["report"])
    return {"label": data["report"]["label"], "verified": True, **calculated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    reports = []
    for filename in args.files:
        with gzip.open(filename, "rt", encoding="utf-8") as source: data = json.load(source)
        reports.append(recompute(data))
    print(json.dumps(reports, indent=2))


if __name__ == "__main__": main()
