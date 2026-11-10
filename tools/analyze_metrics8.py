"""Offline schema-8 diagnostics; no network access and no image data."""
import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
from pathlib import Path

from .analyze_mobile_exports import distribution, finite


def key(row):
    return row.get("session_id"), row.get("seq")


def prefix(a, b):
    return len(a) <= len(b) and a == b[:len(a)]


def difference(row, left, right):
    return row[left]-row[right] if finite(row.get(left)) and finite(row.get(right)) else None


def clock_at(clocks, at):
    index = bisect_right([c["at"] for c in clocks], at)-1
    if index < 0:
        return None
    c = clocks[index]
    return c.get("estimate") if at-c["at"] <= 10000 else None


def derive(data):
    session_map = {s["id"]: s for s in data["sessions"]}
    clocks = {sid: [c for c in data["clocks"] if c["session_id"] == sid] for sid in session_map}
    frames = {key(f): f for f in data["frames"]}
    packets = []
    for p in data["packets"]:
        frame = frames.get(key(p))
        estimate = clock_at(clocks[p["session_id"]], p["received_at_ms"])
        row = dict(p)
        row["session_number"] = int(p["session_id"].rsplit(":", 1)[1])
        row["publication_to_send_ms"] = difference(p, "server_sent_ms", "captured_ms")
        row["receive_to_ack_ms"] = difference(p, "ack_at_ms", "received_at_ms")
        row["receive_to_append_ms"] = difference(p, "append_completed_at_ms", "received_at_ms")
        row["receive_to_decode_ms"] = difference(p, "decode_completed_at_ms", "received_at_ms")
        row["receive_to_present_ms"] = difference(p, "presented_at_ms", "received_at_ms")
        row["clock_rtt_ms"] = estimate.get("rtt") if estimate else None
        row["send_to_receive_estimate_ms"] = (p["received_at_ms"]+estimate["offset"]-p["server_sent_ms"]
                                                 if estimate and finite(p.get("server_sent_ms")) else None)
        row["publication_to_receive_estimate_ms"] = (p["received_at_ms"]+estimate["offset"]-p["captured_ms"]
                                                        if estimate else None)
        # This identity uses the SAME presentation-time offset for all components.
        row["send_to_receive_from_presented_ms"] = (
            frame["latency_ms"]-row["publication_to_send_ms"]-row["receive_to_present_ms"]
            if frame and all(finite(x) for x in (frame.get("latency_ms"), row["publication_to_send_ms"], row["receive_to_present_ms"]))
            else None)
        row["part_total_bytes"] = sum(p["part_bytes"]) if p.get("part_bytes") is not None else None
        row["declared_payload_bytes"] = p.get("declared_payload_bytes")
        if row["declared_payload_bytes"] is None:
            row["declared_payload_bytes"] = row["part_total_bytes"]
        row["payload_shortfall_bytes"] = (row["declared_payload_bytes"]-p["payload_bytes"]
                                             if row["declared_payload_bytes"] is not None else None)
        packets.append(row)
    return packets


def summarize(data, packets, session_ids, start, end, label):
    selected = set(session_ids)
    fs = [f for f in data["frames"] if f["session_id"] in selected and start <= f["at"] <= end]
    ps = [p for p in packets if p["session_id"] in selected and start <= p["at"] <= end]
    ss = [s for s in data["samples"] if s.get("session_id") in selected and start <= s["at"] <= end]
    visible = [s for s in ss if s["visibility"] == "visible" and s["active"]]
    valid = [s for s in visible if finite(s.get("age_ms"))]
    events = [e for e in data["events"] if e.get("session_id") in selected and start <= e["at"] <= end]
    gaps = [{"from_s": a["at"]/1000, "to_s": b["at"]/1000, "gap_ms": b["at"]-a["at"],
             "cross_session": a["session_id"] != b["session_id"]} for a, b in zip(fs, fs[1:])]
    result = {"label": label, "start_ms": start, "end_ms": end, "duration_s": (end-start)/1000,
              "frames": len(fs), "packets": len(ps), "samples": len(ss), "visible_active_samples": len(visible),
              "observed_fps": len(fs)*1000/(end-start), "payload_mbps": sum(p["payload_bytes"] for p in ps)*.008/(end-start),
              "age_ms": distribution([s.get("age_ms") for s in visible]),
              "presentation_latency_ms": distribution([f.get("latency_ms") for f in fs]),
              "buffer_ms": distribution([s.get("buffer_ms") for s in visible]),
              "queue_bytes": distribution([s.get("queue_bytes") for s in visible]),
              "frame_gap_ms": distribution([g["gap_ms"] for g in gaps]),
              "age_over_500_pct": 100*sum(s["age_ms"] > 500 for s in valid)/len(valid) if valid else None,
              "age_over_1000_pct": 100*sum(s["age_ms"] > 1000 for s in valid)/len(valid) if valid else None,
              "events": dict(Counter(e["type"] for e in events)),
              "resyncs": [{"at_s": e["at"]/1000, "message": e["message"]} for e in events if e["type"] == "resync"],
              "longest_gaps": sorted(gaps, key=lambda x: -x["gap_ms"])[:6],
              "dimensions": dict(Counter(f'{p["width"]}x{p["height"]}' for p in ps))}
    for name in ["encode_ms", "publication_to_send_ms", "send_to_receive_estimate_ms", "publication_to_receive_estimate_ms",
                 "send_to_receive_from_presented_ms", "receive_to_present_ms", "receive_to_append_ms", "receive_to_decode_ms",
                 "receive_to_ack_ms", "clock_rtt_ms", "payload_bytes"]:
        result[name] = distribution([p.get(name) for p in ps])
    result["negative_network_estimates"] = sum(finite(p["send_to_receive_estimate_ms"]) and p["send_to_receive_estimate_ms"] < 0 for p in ps)
    result["payload_length_mismatches"] = sum(p["payload_shortfall_bytes"] not in (None, 0) for p in ps)
    return result


def analyze(paths):
    exports = sorted([json.loads(p.read_text(encoding="utf-8-sig")) for p in paths], key=lambda d: d["exported_at_ms"])
    if any(d["schema"] != 8 for d in exports) or len({d["run"]["id"] for d in exports}) != 1:
        raise ValueError("Expected schema 8 exports of the same page run")
    data = exports[-1]
    overlap = []
    for old in exports[:-1]:
        immutable = {name: prefix(old[name], data[name]) for name in ["frames", "samples", "events", "clocks"]}
        if not all(immutable.values()):
            raise ValueError("Conflicting cumulative histories; cannot simply use latest export")
        old_packets = [{k: p.get(k) for k in ("session_id", "seq", "id", "at", "bytes", "payload_bytes")} for p in old["packets"]]
        new_packets = [{k: p.get(k) for k in ("session_id", "seq", "id", "at", "bytes", "payload_bytes")} for p in data["packets"]]
        if not prefix(old_packets, new_packets):
            raise ValueError("Conflicting packet identities")
        overlap.append({"earlier_export_at_ms": old["exported_at_ms"], "exact_prefix": immutable,
                        "shared_frames": len(old["frames"]), "shared_samples": len(old["samples"]), "shared_packets": len(old_packets)})
    sessions = data["sessions"]
    sid_set = {s["id"] for s in sessions}
    packets = derive(data)
    quality = {"overlap": overlap, "retention": data["retention"],
               "duplicate_frame_keys": len(data["frames"])-len({key(f) for f in data["frames"]}),
               "duplicate_packet_keys": len(packets)-len({key(p) for p in packets}),
               "orphan_records": {name: sum(r.get("session_id") is not None and r["session_id"] not in sid_set for r in data[name])
                                  for name in ["frames", "packets", "samples", "events", "clocks"]},
               "nonmonotonic_timestamps": {name: sum(b["at"] < a["at"] for a, b in zip(data[name], data[name][1:]))
                                          for name in ["frames", "packets", "samples", "events", "clocks"]},
               "visibility_counts": dict(Counter(s["visibility"] for s in data["samples"])),
               "sampling_interval_ms": distribution([s["interval_ms"] for s in data["samples"]]),
               "sampling_gaps_over_750ms": sum((s.get("interval_ms") or 0) > 750 for s in data["samples"]),
               "recorded_conditions": data["conditions"], "session_config_mismatches": [],
               "sample_codec_mismatches": [], "frame_packet_missing": []}
    smap = {s["id"]: s for s in sessions}
    for s in sessions:
        if any(s["config"][k] != s["server_config"][k] for k in ("codec", "kbps", "fps", "semantic")):
            quality["session_config_mismatches"].append(s["id"])
    quality["sample_codec_mismatches"] = [s["at"] for s in data["samples"] if s.get("session_id") and s["codec"] != smap[s["session_id"]]["config"]["codec"]]
    packet_keys = {key(p) for p in packets}
    quality["frame_packet_missing"] = [key(f) for f in data["frames"] if key(f) not in packet_keys]
    groups = []
    for s in sessions:
        codec = s["config"]["codec"]
        if not groups or groups[-1]["codec"] != codec:
            if groups:
                groups[-1]["end"] = s["started_at_ms"]
            groups.append({"codec": codec, "ids": [], "start": s["started_at_ms"], "end": data["exported_at_ms"]})
        groups[-1]["ids"].append(s["id"])
    profiles = [summarize(data, packets, g["ids"], g["start"], g["end"], g["codec"]) for g in groups]
    per_session = []
    for s in sessions:
        item = summarize(data, packets, [s["id"]], s["started_at_ms"], s["ended_at_ms"] or data["exported_at_ms"], s["id"].rsplit(":", 1)[1])
        item.update(codec=s["config"]["codec"], start_reason=s["start_reason"], end_reason=s["end_reason"], config=s["config"])
        per_session.append(item)
    invalid = []
    for p in packets:
        if p["payload_shortfall_bytes"] in (None, 0):
            continue
        events = [e for e in data["events"] if e.get("session_id") == p["session_id"] and e["type"] == "resync" and e["at"] >= p["at"]]
        invalid.append({k: p[k] for k in ["session_number", "seq", "codec", "at", "declared_payload_bytes", "payload_bytes",
                                        "payload_shortfall_bytes", "publication_to_send_ms", "send_to_receive_estimate_ms"]})
        invalid[-1]["resync_after_ms"] = events[0]["at"]-p["at"] if events else None
        invalid[-1]["resync_reason"] = events[0]["message"] if events else None
    worst = []
    for codec in dict.fromkeys(g["codec"] for g in groups):
        selected = sorted([p for p in packets if p["codec"] == codec and finite(p["send_to_receive_estimate_ms"])], key=lambda p: -p["send_to_receive_estimate_ms"])[:8]
        worst.extend({k: p[k] for k in ["session_number", "seq", "codec", "at", "publication_to_send_ms",
                                       "send_to_receive_estimate_ms", "receive_to_present_ms", "clock_rtt_ms", "payload_shortfall_bytes"]} for p in selected)
    result = {"schema": 1, "input_files": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
              "source_run": {"run_id": data["run"]["id"], "port": data["run"]["endpoint"]["port"],
                             "app_version": data["run"]["app_version"], "recorder_version": data["run"]["recorder_version"],
                             "exported_at_utc": data["exported_at_utc"], "source_sha256": sorted({s["implementation"]["source_sha256"] for s in sessions})},
              "quality": quality, "profiles": profiles, "sessions": per_session,
              "invalid_payload_packets": invalid,
              "invalid_jpeg_packets": [p for p in invalid if p["codec"] == "jpeg"],
              "largest_network_estimates": worst,
              "events": dict(Counter(e["type"] for e in data["events"])),
              "session_starts": dict(Counter(s["start_reason"] for s in sessions)),
              "auto_reconnects_export": data["reconnects"]}
    return result, data, packets


def notebook(paths):
    names = [p.name for p in paths]
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
            "cells": [{"cell_type": "markdown", "metadata": {}, "source": ["# Schema 8 offline diagnostics\n", "Run from the repository root. Set the folder containing the original exports. No Pi connection.\n"]},
                      {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                       "source": ["from pathlib import Path\n", "from tools.analyze_metrics8 import analyze\n", "folder = Path('INPUT_FOLDER')\n",
                                  f"paths = [folder / name for name in {names!r}]\n", "result, data, packets = analyze(paths)\n",
                                  "result['profiles'], result['quality'], result['invalid_jpeg_packets']\n"]}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result, _, _ = analyze(args.inputs)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    (args.output/"analysis.ipynb").write_text(json.dumps(notebook(args.inputs), indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"quality": result["quality"], "profiles": result["profiles"], "invalid_jpeg_packets": result["invalid_jpeg_packets"],
                      "session_starts": result["session_starts"], "events": result["events"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
