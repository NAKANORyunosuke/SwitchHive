from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class Keypoint:
    x: float
    y: float
    score: float


@dataclass
class PoseResult:
    keypoints: List[Keypoint]
    bbox: Tuple[int, int, int, int]  # x, y, w, h of the person
    score: float


class PoseEstimator:
    def estimate(self, frame: np.ndarray) -> Optional[PoseResult]:
        raise NotImplementedError


class MediapipePoseEstimator(PoseEstimator):
    def __init__(self) -> None:
        # lazy import
        import mediapipe as mp  # type: ignore

        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(static_image_mode=False, model_complexity=1)

    def estimate(self, frame: np.ndarray) -> Optional[PoseResult]:
        import mediapipe as mp  # type: ignore

        ih, iw = frame.shape[:2]
        rgb = frame[:, :, ::-1]
        res = self.pose.process(rgb)
        if not res.pose_landmarks:
            return None
        kps: List[Keypoint] = []
        xs: List[int] = []
        ys: List[int] = []
        lm = res.pose_landmarks.landmark
        # Use a subset of landmarks (shoulders, hips, knees, etc.)
        indices = list(range(len(lm)))
        for i in indices:
            p = lm[i]
            x = int(p.x * iw)
            y = int(p.y * ih)
            xs.append(x)
            ys.append(y)
            kps.append(Keypoint(x=float(x), y=float(y), score=float(p.visibility)))
        x0, x1 = max(0, min(xs)), min(iw - 1, max(xs))
        y0, y1 = max(0, min(ys)), min(ih - 1, max(ys))
        bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        score = float(np.mean([kp.score for kp in kps]))
        return PoseResult(keypoints=kps, bbox=bbox, score=score)


def build_estimator(backend: str) -> PoseEstimator:
    b = backend.lower()
    if b == "mediapipe":
        return MediapipePoseEstimator()
    # Future: implement tflite/onnx/opencv-dnn backends
    raise SystemExit(101)

