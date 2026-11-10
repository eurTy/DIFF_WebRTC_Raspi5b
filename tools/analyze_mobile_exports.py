"""Offline analysis of cumulative phone exports; never contacts the Pi."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def quantile(values, p):
    ordered = sorted(v for v in values if finite(v))
    if not ordered:
        return None
    index = (len(ordered)-1)*p
    low, high = math.floor(index), math.ceil(index)
    return ordered[low]+(ordered[high]-ordered[low])*(index-low)


def distribution(values):
    valid = [v for v in values if finite(v)]
    return {"count": len(valid), "missing": len(values)-len(valid),
            "min": min(valid) if valid else None,
            "p50": quantile(valid, .5), "p95": quantile(valid, .95),
            "p99": quantile(valid, .99), "max": max(valid) if valid else None,
            "mean": statistics.fmean(valid) if valid else None}


def prefix_count(a, b):
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def profile(label, frames, samples, reconnects=None):
    if not frames or not samples:
        raise ValueError("A segment needs both frame and sample records")
    start = min(samples[0]["at"], frames[0]["at"])
    end = max(samples[-1]["at"], frames[-1]["at"])
    duration = (end-start)/1000
    gaps = [{"start_s": (a["at"]-start)/1000,
             "end_s": (b["at"]-start)/1000,
             "gap_ms": b["at"]-a["at"],
             "sequence_reset": b["seq"] <= a["seq"]}
            for a, b in zip(frames, frames[1:])]
    sample_gaps = [b["at"]-a["at"] for a, b in zip(samples, samples[1:])]
    ages = [s["age_ms"] for s in samples if finite(s.get("age_ms"))]
    resets = [{"at_s": (b["at"]-start)/1000, "old_seq": a["seq"], "new_seq": b["seq"],
               "presentation_gap_ms": b["at"]-a["at"]}
              for a, b in zip(frames, frames[1:]) if b["seq"] <= a["seq"]]
    severe = sorted([g for g in gaps if g["gap_ms"] > 500], key=lambda g: -g["gap_ms"])
    stale_with_empty_buffer = [s for s in samples if finite(s.get("age_ms")) and
                              s["age_ms"] > 500 and finite(s.get("buffer_ms")) and
                              s["buffer_ms"] < 50]
    return {
        "label": label, "window_start_ms": start, "window_end_ms": end,
        "duration_s": duration, "sample_span_s": (samples[-1]["at"]-samples[0]["at"])/1000,
        "frames": len(frames), "samples": len(samples),
        "observed_fps": len([f for f in frames if start <= f["at"] <= end])/duration,
        "presented_frame_span_fps": (len(frames)-1)*1000/(frames[-1]["at"]-frames[0]["at"]),
        "age_ms": distribution([s.get("age_ms") for s in samples]),
        "presentation_latency_ms": distribution([f.get("latency_ms") for f in frames]),
        "display_fps": distribution([s.get("display_fps") for s in samples]),
        "buffer_ms": distribution([s.get("buffer_ms") for s in samples]),
        "encode_ms": distribution([f.get("encode_ms") for f in frames]),
        "frame_interval_ms": distribution([g["gap_ms"] for g in gaps]),
        "sample_interval_ms": distribution(sample_gaps),
        "sample_gaps_over_750ms": sum(g > 750 for g in sample_gaps),
        "sampling_gap_excess_over_250ms_s": sum(max(0, g-250) for g in sample_gaps if g > 750)/1000,
        "width_counts": dict(Counter(f["width"] for f in frames)),
        "reconnects_export_total": reconnects, "sequence_resets": resets,
        "age_over_500ms_percent_of_valid_samples": sum(a > 500 for a in ages)/len(ages)*100 if ages else None,
        "age_over_1000ms_percent_of_valid_samples": sum(a > 1000 for a in ages)/len(ages)*100 if ages else None,
        "zero_fps_samples": sum(s.get("display_fps") == 0 for s in samples),
        "stale_over_500ms_and_buffer_below_50ms_samples": len(stale_with_empty_buffer),
        "stale_over_500ms_with_known_buffer_samples": sum(finite(s.get("age_ms")) and
            s["age_ms"] > 500 and finite(s.get("buffer_ms")) for s in samples),
        "gaps_over_500ms": len(severe), "gaps_over_1000ms": sum(g["gap_ms"] > 1000 for g in gaps),
        "longest_gaps": severe[:12],
        "frame_timestamp_reversals": sum(b["at"] < a["at"] for a, b in zip(frames, frames[1:])),
        "sample_timestamp_reversals": sum(b["at"] < a["at"] for a, b in zip(samples, samples[1:])),
        "duplicate_frame_rows": len(frames)-len({json.dumps(f, sort_keys=True) for f in frames}),
    }


def analyze(paths):
    data = [json.loads(path.read_text(encoding="utf-8-sig")) for path in paths]
    experiment, baseline, cumulative = data
    shared_frames = prefix_count(baseline["frames"], cumulative["frames"])
    shared_samples = prefix_count(baseline["samples"], cumulative["samples"])
    if shared_frames != len(baseline["frames"]) or shared_samples != len(baseline["samples"]):
        raise ValueError("The expected cumulative export relationship did not hold")
    # The export has no per-frame codec. Here the observed width change and
    # reset delimit JPEG, confirmed against the existing encoder implementation.
    jpeg_index = next(i for i, f in enumerate(cumulative["frames"]) if f["width"] != 1280)
    if jpeg_index < shared_frames:
        raise ValueError("JPEG begins inside the H.264 baseline")
    jpeg_frames = cumulative["frames"][jpeg_index:]
    if not all(f["width"] == 640 for f in jpeg_frames):
        raise ValueError("Unexpected JPEG base width change; revise segment logic")
    jpeg_start = jpeg_frames[0]["at"]
    jpeg_samples = [s for s in cumulative["samples"] if s["at"] >= jpeg_start]
    segments = [
        ("8091 H.264", experiment["frames"], experiment["samples"], experiment["reconnects"]),
        ("8090 H.264", baseline["frames"], baseline["samples"], baseline["reconnects"]),
        ("8090 JPEG (after codec switch)", jpeg_frames, jpeg_samples, None),
    ]
    profiles = [profile(*segment) for segment in segments]
    warm = []
    for label, frames, samples, _ in segments:
        start = max(frames[0]["at"], samples[0]["at"])+5000
        warm.append(profile(label+" (first 5 s excluded)", [f for f in frames if f["at"] >= start],
                            [s for s in samples if s["at"] >= start]))
    raw_profiles = [profile(path.name, item["frames"], item["samples"], item["reconnects"])
                    for path, item in zip(paths, data)]
    summary = {
        "schema": 1, "analysis_date": "2026-10-18", "contacted_pi": False,
        "port_attribution": "User supplied; export lacks port and build id",
        "method": {"quantiles": "Linear interpolation, rank (n-1)*p; finite values only",
                   "age_scope": "Sample-weighted current-frame age; not glass-to-glass or a wall-time proportion",
                   "fps_scope": "Presentations divided by the union of frame/sample elapsed windows; interruptions included",
                   "gap_scope": "No recorded presentation; pause/background/reconnect cannot be separated without events",
                   "jpeg_scope": "From first 640-wide frame onward; first JPEG-adjacent samples may include transition"},
        "files": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                   "schema": d["schema"], "final_config": d["config"], "source": d.get("source")}
                  for p, d in zip(paths, data)],
        "overlap": {"baseline_frames_exact_prefix": shared_frames, "baseline_samples_exact_prefix": shared_samples,
                    "extra_h264_frames_after_baseline_export": jpeg_index-shared_frames,
                    "jpeg_first_frame_at_ms": jpeg_start,
                    "jpeg_first_frame": jpeg_frames[0], "last_h264_before_switch": cumulative["frames"][jpeg_index-1]},
        "segments": profiles, "first_5_seconds_excluded": warm, "raw_export_profiles": raw_profiles,
    }
    return summary, segments


def plot(segments, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(3, 2, figsize=(12, 8.5), layout="constrained", sharey="col")
    colors = ["#166c94", "#28836d", "#9c6430"]
    for row, ((label, frames, samples, _), color) in enumerate(zip(segments, colors)):
        origin = samples[0]["at"]
        x = [(s["at"]-origin)/1000 for s in samples]
        y = [s["age_ms"] if finite(s.get("age_ms")) else np.nan for s in samples]
        # A timer gap is missing coverage, not a measured linear transition.
        xx, yy, ff = [], [], []
        for i, (t, age) in enumerate(zip(x, y)):
            if i and t-x[i-1] > .75:
                xx.append(np.nan); yy.append(np.nan); ff.append(np.nan)
                for ax in axes[row]:
                    ax.axvspan(x[i-1], t, color="#d6d9dc", alpha=.6)
            xx.append(t); yy.append(age); ff.append(samples[i]["display_fps"])
        axes[row, 0].plot(xx, yy, color=color, lw=1.5)
        axes[row, 0].axhline(500, color="#a6353e", ls="--", lw=.9)
        axes[row, 0].set_ylabel(label+"\nFrame age (ms)")
        axes[row, 1].plot(xx, ff, color=color, lw=1.5)
        axes[row, 1].axhline(20, color="#747d84", ls="--", lw=.9)
        for ax in axes[row]:
            ax.set_xlabel("Seconds in segment")
            ax.grid(axis="y", alpha=.18)
            ax.spines[["top", "right"]].set_visible(False)
            ax.set_xlim(0, x[-1])
    axes[0, 0].set_title("Frame age, including stalls")
    axes[0, 1].set_title("Displayed fps (2-second moving count)")
    axes[0, 0].set_ylim(bottom=0)
    axes[0, 1].set_ylim(0, 22)
    fig.suptitle("Phone 4G exports: 8091 vs 8090\nUnequal sequential runs; gray = missing sample coverage", fontsize=14)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def notebook(output):
    code = ("from pathlib import Path\nimport sys\n"
            "repo = Path.cwd()\n"
            "while not (repo / 'tools' / 'analyze_mobile_exports.py').exists():\n"
            "    if repo == repo.parent: raise RuntimeError('Open from the project tree')\n"
            "    repo = repo.parent\n"
            "sys.path.insert(0, str(repo / 'tools'))\n"
            "from analyze_mobile_exports import analyze\n"
            "# Set this to the directory containing the three user exports.\n"
            "source_dir = Path('SOURCE_EXPORT_DIRECTORY')\n"
            "paths = [source_dir / name for name in ['mobile-v05.json', 'mobile-v05 (2).json', 'mobile-v05 (3).json']]\n"
            "summary, segments = analyze(paths)\n"
            "[(s['label'], s['age_ms'], s['observed_fps']) for s in summary['segments']]")
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
          "cells": [{"cell_type": "markdown", "id": "measurement-scope", "metadata": {},
                     "source": ["# Phone export analysis\n", "Offline reproduction. Ports were supplied by the user. The JPEG export contains the H.264 prefix; analyze only the 640-wide segment. Null clock samples are excluded from quantiles, not hidden. Sample gaps and reconnects remain reported.\n"]},
                    {"cell_type": "code", "id": "reproduce", "metadata": {}, "execution_count": None,
                     "outputs": [], "source": code.splitlines(keepends=True)}]}
    output.write_text(json.dumps(nb, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs=3, type=Path, help="8091 H264, 8090 H264, 8090 cumulative JPEG")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    summary, segments = analyze(args.files)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    notebook(args.output/"analysis.ipynb")
    if args.plot:
        plot(segments, args.output/"phone-timelines.png")
    print(json.dumps({"overlap": summary["overlap"], "segments": summary["segments"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
