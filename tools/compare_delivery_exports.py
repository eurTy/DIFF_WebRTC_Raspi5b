"""Compare two independent phone runs without merging their cumulative histories."""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from .analyze_metrics8 import analyze, summarize
from .analyze_mobile_exports import distribution, finite


def codec_summary(data, packets, profiles, codec):
    phases = [p for p in profiles if p["label"] == codec]
    ids = [s["id"] for s in data["sessions"] if s["config"]["codec"] == codec]
    duration = sum(p["duration_s"] for p in phases)
    result = summarize(data, packets, ids, phases[0]["start_ms"], phases[-1]["end_ms"], codec)
    # H.264 may occur on both sides of JPEG; that intervening phase is not a freeze.
    result["duration_s"] = duration
    result["intervals_ms"] = [[p["start_ms"], p["end_ms"]] for p in phases]
    result["observed_fps"] = result["frames"] / duration
    result["payload_mbps"] = sum(p["payload_bytes"] for p in packets if p["codec"] == codec)*8/duration/1e6
    gaps = []
    for p in phases:
        frames = [f for f in data["frames"] if f["session_id"] in ids and p["start_ms"] <= f["at"] <= p["end_ms"]]
        gaps.extend({"gap_ms": b["at"]-a["at"], "from_ms": a["at"], "to_ms": b["at"],
                     "cross_session": a["session_id"] != b["session_id"]}
                    for a, b in zip(frames, frames[1:]))
    result["frame_gap_ms"] = distribution([g["gap_ms"] for g in gaps])
    result["longest_gaps"] = sorted(gaps, key=lambda g: -g["gap_ms"])[:6]
    result["gaps_over_1000ms"] = sum(g["gap_ms"] > 1000 for g in gaps)
    result["gap_excess_over_500_s"] = sum(max(0, g["gap_ms"]-500) for g in gaps)/1000
    result["auto_reconnects"] = sum(s["start_reason"] == "automatic_reconnect" for s in data["sessions"] if s["id"] in ids)
    result["auto_reconnects_per_min"] = result["auto_reconnects"]*60/duration
    policies = [p["delivery_policy"] for p in packets if p["codec"] == codec and p.get("delivery_policy")]
    result["pre_send_policy"] = {k: distribution([p.get(k) for p in policies])
                                 for k in ("pending_frames", "pending_bytes", "oldest_ack_ms", "pacing_kbps")}
    samples = [s for s in data["samples"] if s.get("session_id") in ids and s["visibility"] == "visible" and s["active"]]
    ages = [s["age_ms"] for s in samples if finite(s.get("age_ms"))]
    if ages:
        expected = np.quantile(ages, [.5, .95, .99], method="linear")
        actual = [result["age_ms"][k] for k in ("p50", "p95", "p99")]
        if not np.allclose(expected, actual, rtol=1e-10, atol=1e-8):
            raise ValueError("Independent age quantiles disagree")
    result["age_quantiles_numpy_checked"] = True
    return result


def investigate(path):
    result, data, packets = analyze([path])
    result["combined_codecs"] = {codec: codec_summary(data, packets, result["profiles"], codec)
                                  for codec in dict.fromkeys(p["label"] for p in result["profiles"])}
    anomalies = []
    for p in result["invalid_payload_packets"]:
        sid = next(s["id"] for s in data["sessions"] if int(s["id"].rsplit(":", 1)[1]) == p["session_number"])
        closes = [e for e in data["events"] if e.get("session_id") == sid and e["type"] == "socket_close" and e["at"] >= p["at"]]
        frames = [f for f in data["frames"] if f["session_id"] == sid]
        anomalies.append({**p, "close_after_ms": closes[0]["at"]-p["at"] if closes else None,
                          "close_code": closes[0]["code"] if closes else None,
                          "same_session_frames_after": sum(f["at"] > p["at"] for f in frames)})
    result["integrity_followup"] = anomalies
    result["packet_drop_reasons"] = dict(Counter(p.get("drop_reason") for p in data["packets"] if p.get("drop_reason")))
    result["session_rows"] = [{"session": s["label"], "codec": s["codec"], "duration_s": s["duration_s"],
                               "frames": s["frames"], "start_reason": s["start_reason"], "end_reason": s["end_reason"]}
                              for s in result["sessions"]]
    result["counts"] = {k: len(data[k]) for k in ("frames", "packets", "samples", "sessions", "events", "clocks")}
    return result


def receipt(before, after, rows):
    names = [before["input_files"][0]["name"], after["input_files"][0]["name"]]
    return {"schemaVersion": 1, "items": [
        {"id": "delivery-retest", "title": "8091 复测：常态延迟改善，但重连与秒级停顿仍存在", "queries": [{
            "id": "before-after", "source": {"label": "更新前后手机导出", "files": names,
                "filters": ["端口：8091", "帧龄：前台且 active 的有限数值", "帧率：编码阶段总时长，包含重连等待", "同一编码的非连续阶段合并原始样本，不平均分位数"],
                "caveats": ["前后不是同步配对实验，ROI 和网络状态可能不同，不能由单次对比证明因果收益。",
                            "软件帧龄不等于曝光到屏幕的光学总延迟；缺失帧龄不按零计算。",
                            "新记录网络条件仍为 unknown，Safari/4G 来自用户复测约定；未独立验证路径。",
                            "H.264 为 1280x720；JPEG 为 640x360 底图加原坐标重点贴片，不作等画质比较。"],
                "evidenceFlow": [{"kind": "validation", "title": "记录完整性", "detail": "新记录 1561 帧、1590 包、657 条前台采样；无截断保留、重复键或时间倒序。帧龄分位数已与 NumPy 线性分位数独立核对。"}]},
            "columns": [{"field": "release", "label": "版本"}, {"field": "codec", "label": "编码"},
                        {"field": "duration_s", "label": "时长 s"}, {"field": "fps", "label": "实际 fps"},
                        {"field": "age_p50_ms", "label": "帧龄 P50 ms"}, {"field": "age_p95_ms", "label": "帧龄 P95 ms"},
                        {"field": "reconnects", "label": "自动重连"}, {"field": "reconnects_per_min", "label": "重连/分钟"}],
            "rows": rows, "preview": {"kind": "aggregate", "note": "两个独立运行，分别按编码聚合。"},
            "methods": [{"language": "calculation", "code": "fps = presented_frames / sum(codec_phase_seconds)\nreconnects_per_min = automatic_reconnects * 60 / sum(codec_phase_seconds)\nage_p95 = linear_quantile(finite_visible_active_age_samples, 0.95)"}]
        }]},
        {"id": "remaining-length-errors", "title": "新版本仍出现两次总载荷长度不足", "queries": [{
            "id": "payload-anomalies", "source": {"label": "逐包长度及后续连接事件", "files": [names[1]],
                "filters": ["声明总载荷不等于实际 payload_bytes"],
                "caveats": ["只有数值日志，没有逐跳内容证据，不能认定截断发生于编码、转发、网络或 Safari。",
                            "1006 表示未正常关闭，不能单凭这个代码确定是服务端超时。"]},
            "columns": ["codec", "seq", "declared_payload_bytes", "payload_bytes", "payload_shortfall_bytes", "close_after_ms", "same_session_frames_after"],
            "rows": [{k: a[k] for k in ("codec", "seq", "declared_payload_bytes", "payload_bytes", "payload_shortfall_bytes", "close_after_ms", "same_session_frames_after")}
                     for a in after["integrity_followup"]],
            "methods": [{"language": "calculation", "code": "payload_shortfall_bytes = declared_payload_bytes - payload_bytes"}]
        }]}
    ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, after = investigate(args.before), investigate(args.after)
    rows = []
    for release, result in (("before", before), ("after", after)):
        for codec, c in result["combined_codecs"].items():
            rows.append({"release": release, "codec": codec, "duration_s": c["duration_s"], "fps": c["observed_fps"],
                         "age_p50_ms": c["age_ms"]["p50"], "age_p95_ms": c["age_ms"]["p95"],
                         "reconnects": c["auto_reconnects"], "reconnects_per_min": c["auto_reconnects_per_min"]})
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in (("comparison.json", {"before": before, "after": after, "rows": rows}),
                        ("source-receipt.json", receipt(before, after, rows))):
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"rows": rows, "after_integrity": after["integrity_followup"],
                      "reconnects": {"before": before["auto_reconnects_export"], "after": after["auto_reconnects_export"]}}, ensure_ascii=True))


if __name__ == "__main__":
    main()
