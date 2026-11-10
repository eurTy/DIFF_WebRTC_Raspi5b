"""Join phone integrity events to bounded server evidence; never infer a culprit."""
import argparse
import json
from pathlib import Path

from .analyze_metrics8 import clock_at


def correlate(data):
    trace = data.get("server_diagnostics")
    if not trace:
        raise ValueError("Export has no server_diagnostics; use the enhanced download button")
    sessions = {s["id"]: s for s in data["sessions"]}
    packets = {(p["session_id"], p["seq"]): p for p in data["packets"]}
    server_sessions = {s["server_session_id"]: s for s in trace["sessions"]}
    faults = []
    for event in data["events"]:
        if event["type"] != "integrity_rejected":
            continue
        session = sessions.get(event["session_id"], {})
        sid, seq = session.get("server_session_id"), event.get("seq")
        packet = packets.get((event["session_id"], seq), {})
        rows = [e for e in trace["events"] if e["server_session_id"] == sid]
        writes = [e for e in rows if e["event"] == "write_start" and e.get("seq") == seq]
        sent = writes[-1] if writes else None
        estimate = clock_at([c for c in data["clocks"] if c["session_id"] == event["session_id"]], event["at"])
        estimated_received = event["at"]+estimate["offset"] if estimate else None
        fault = {"client_session_id": event["session_id"], "server_session_id": sid, "seq": seq,
                 "codec": session.get("config", {}).get("codec"), "reason": event.get("reason"),
                 "declared_bytes": packet.get("declared_payload_bytes"), "actual_bytes": packet.get("payload_bytes"),
                 "client_expected_crc32": packet.get("payload_crc32"), "client_actual_crc32": packet.get("actual_crc32"),
                 "server_write": sent,
                 "write_buffered": [e for e in rows if e["event"] == "write_buffered" and e.get("seq") == seq],
                 "feedback": [e for e in rows if e.get("seq") == seq and e["event"] in ("ack", "client_report")],
                 "termination": server_sessions.get(sid),
                 "abort_events": [e for e in rows if e["event"] == "abort"],
                 "receive_estimate_server_ms": estimated_received}
        fault["server_header_matches_client_header"] = (
            sent["expected_bytes"] == fault["declared_bytes"] and sent["expected_crc32"] == fault["client_expected_crc32"]
            if sent else None)
        faults.append(fault)
    return {"schema": 1, "boot_id": trace["boot_id"], "server_trace_selection": trace.get("selection"),
            "server_trace_discarded": trace["discarded"], "fault_count": len(faults), "faults": faults,
            "sessions": trace["sessions"],
            "limits": ["Write buffered does not prove delivery to the peer.",
                       "Matching sender/header but shorter client payload localizes an observed mismatch, not its network hop.",
                       "Cross-clock ordering uses symmetric minimum-RTT estimation; simultaneous events are not causal proof.",
                       "No recorded faults does not prove an error-free or fully retained run."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = correlate(json.loads(args.input.read_text(encoding="utf-8-sig")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"fault_count": result["fault_count"], "trace_discarded": result["server_trace_discarded"]}))


if __name__ == "__main__":
    main()
