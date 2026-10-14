import json
import struct

MAGIC = b"SVP2"
MAX_META = 4096
MAX_PIECE = 2 * 1024 * 1024


def pack_piece(metadata, jpeg):
    raw = json.dumps(metadata, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    if not 2 <= len(raw) <= MAX_META or not jpeg.startswith(b"\xff\xd8"):
        raise ValueError("Invalid piece")
    packet = struct.pack("!4sI", MAGIC, len(raw)) + raw + jpeg
    if len(packet) > MAX_PIECE:
        raise ValueError("Piece too large")
    return packet


def unpack_piece(packet):
    if not 12 <= len(packet) <= MAX_PIECE:
        raise ValueError("Invalid piece size")
    magic, size = struct.unpack_from("!4sI", packet)
    if magic != MAGIC or not 2 <= size <= MAX_META or size + 10 > len(packet):
        raise ValueError("Invalid piece header")
    meta = json.loads(packet[8:8 + size])
    if not isinstance(meta, dict) or meta.get("kind") not in ("base", "patch"):
        raise ValueError("Invalid piece metadata")
    image = packet[8 + size:]
    if not image.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG")
    return meta, image
