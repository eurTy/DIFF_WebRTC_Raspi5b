import json
import math
import struct

import cv2
import numpy as np

MAGIC = b"SVP1"
MAX_PACKET = 4 * 1024 * 1024
MAX_META = 48 * 1024


def pack(metadata, images):
    payload = dict(metadata)
    payload["lengths"] = [len(data) for data in images]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > MAX_META:
        raise ValueError("Metadata too large")
    message = struct.pack("!4sI", MAGIC, len(raw)) + raw + b"".join(images)
    if len(message) > MAX_PACKET:
        raise ValueError("Packet too large")
    return message


def unpack(message):
    if not 8 <= len(message) <= MAX_PACKET:
        raise ValueError("Invalid packet size")
    magic, size = struct.unpack_from("!4sI", message)
    if magic != MAGIC or not 2 <= size <= MAX_META or size + 8 > len(message):
        raise ValueError("Invalid protocol header")
    meta = json.loads(message[8:8+size])
    if not isinstance(meta, dict) or not isinstance(meta.get("parts"), list):
        raise ValueError("Invalid metadata schema")
    lengths = meta.get("lengths")
    if not isinstance(lengths, list) or not 1 <= len(lengths) <= 5 or len(lengths) != len(meta["parts"]):
        raise ValueError("Invalid part count")
    if any(isinstance(n, bool) or not isinstance(n, int) or not 4 <= n <= MAX_PACKET for n in lengths):
        raise ValueError("Invalid image lengths")
    if sum(lengths) != len(message) - size - 8 or meta.get("width") != 1280 or meta.get("height") != 720:
        raise ValueError("Invalid dimensions or trailing bytes")
    for key in ("captured_ms", "sent_ms", "encode_ms"):
        if isinstance(meta.get(key), bool) or not isinstance(meta.get(key), (int, float)) or not math.isfinite(meta[key]) or meta[key] < 0:
            raise ValueError("Invalid timestamp")
    if isinstance(meta.get("id"), bool) or not isinstance(meta.get("id"), int) or meta["id"] < 0 or meta["id"] >= 2**53:
        raise ValueError("Invalid identity")
    offset, images = size + 8, []
    for index, (part, length) in enumerate(zip(meta["parts"], lengths)):
        if not isinstance(part, dict) or not isinstance(part.get("rect"), list) or len(part["rect"]) != 4:
            raise ValueError("Invalid part rectangle")
        x, y, w, h = part["rect"]
        if any(isinstance(n, bool) or not isinstance(n, int) for n in (x, y, w, h)) or x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1280 or y+h > 720:
            raise ValueError("Part outside canvas")
        if index == 0 and [x, y, w, h] != [0, 0, 1280, 720]:
            raise ValueError("Self-contained full base required")
        data = message[offset:offset+length]
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("Expected JPEG")
        images.append(data)
        offset += length
    return meta, images


def reconstruct(meta, images):
    canvas = None
    for part, raw in zip(meta["parts"], images):
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[0] > 720 or image.shape[1] > 1280:
            raise ValueError("Invalid decoded image")
        x, y, w, h = part["rect"]
        resized = cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)
        if canvas is None:
            canvas = resized
        else:
            canvas[y:y+h, x:x+w] = resized
    return canvas
