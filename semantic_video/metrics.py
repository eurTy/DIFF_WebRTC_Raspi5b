"""Receiver-side fixture metrics; never included as encoding or recognition ground truth."""
import math

import cv2
import numpy as np

from .model import DEFAULT_REGIONS


def psnr(a, b):
    mse = float(np.square(a.astype(np.float32) - b.astype(np.float32)).mean())
    return 99.0 if mse == 0 else 10 * math.log10(255**2 / mse)


def marker(image):
    b, g, r = [channel.astype(np.float32) for channel in cv2.split(image)]
    found = []
    for state, mask in (("red", (r > 95) & (r > 1.3*g) & (r > 1.3*b)),
                        ("green", (g > 95) & (g > 1.3*r) & (g > 1.3*b))):
        count, _, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] >= 5:
                found.append((int(stats[index, cv2.CC_STAT_AREA]), state, centers[index]))
    if not found:
        return None
    _, state, center = max(found, key=lambda item: item[0])
    return state, center


def evaluate(reference, decoded, regions=DEFAULT_REGIONS):
    result, mask = {}, np.ones(reference.shape[:2], dtype=bool)
    for region in regions:
        x, y, w, h = region.rect(1280, 720)
        truth, observed = reference[y:y+h, x:x+w], decoded[y:y+h, x:x+w]
        a, b = marker(truth), marker(observed)
        error = float(np.linalg.norm(a[1]-b[1])) if a and b else None
        result[region.name] = {"psnr_db": psnr(truth, observed), "marker_error_px": error,
                               "marker_correct": bool(a and b and a[0] == b[0] and error <= 3),
                               "weight": region.weight}
        mask[y:y+h, x:x+w] = False
    return {"regions": result, "background_psnr_db": psnr(reference[mask], decoded[mask]),
            "whole_psnr_db": psnr(reference, decoded),
            "weighted_roi_psnr_db": sum(r["weight"] * r["psnr_db"] for r in result.values()) / sum(r.weight for r in regions)}
