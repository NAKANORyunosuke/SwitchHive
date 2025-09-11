from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np

from .pose import Keypoint, PoseResult

# Minimal set of MediaPipe Pose connections for visualization
MEDIAPIPE_EDGES = [
    (11, 12),  # shoulders
    (23, 24),  # hips
    (11, 23), (12, 24),  # torso sides
    (11, 13), (13, 15),  # left arm
    (12, 14), (14, 16),  # right arm
    (23, 25), (25, 27),  # left leg
    (24, 26), (26, 28),  # right leg
]


def draw_pose(frame: np.ndarray, pose: PoseResult, color=(0, 255, 0)) -> np.ndarray:
    out = frame.copy()
    # Draw skeleton lines
    for a, b in MEDIAPIPE_EDGES:
        if a < len(pose.keypoints) and b < len(pose.keypoints):
            pa, pb = pose.keypoints[a], pose.keypoints[b]
            if pa.score >= 0.3 and pb.score >= 0.3:
                cv2.line(out, (int(pa.x), int(pa.y)), (int(pb.x), int(pb.y)), color, 2, lineType=cv2.LINE_AA)
    # Draw keypoints
    for kp in pose.keypoints:
        if kp.score >= 0.3:
            cv2.circle(out, (int(kp.x), int(kp.y)), 3, color, -1, lineType=cv2.LINE_AA)
    # Draw bounding box
    x, y, w, h = pose.bbox
    cv2.rectangle(out, (x, y), (x + w, y + h), (0, 200, 0), 2)
    return out

def draw_hud_text(img: np.ndarray, lines, origin=(10, 20), color=(255, 255, 255)) -> np.ndarray:
    out = img.copy()
    x, y = origin
    for i, text in enumerate(lines):
        yy = y + i * 18
        # shadow
        cv2.putText(out, text, (x+1, yy+1), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(out, text, (x, yy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def face_blur(frame: np.ndarray, kernel: int = 31) -> np.ndarray:
    # Haar cascade based
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.2, 5)
    out = frame.copy()
    for (x, y, w, h) in faces:
        roi = out[y : y + h, x : x + w]
        if roi.size == 0:
            continue
        k = max(3, kernel | 1)  # ensure odd
        roi = cv2.GaussianBlur(roi, (k, k), 0)
        out[y : y + h, x : x + w] = roi
    return out
