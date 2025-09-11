from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .config import DetectionConfig
from .pose import Keypoint, PoseResult


@dataclass
class Features:
    theta: float
    ratio: float
    hip_y: float
    h_person: float


@dataclass
class TriggerSnapshot:
    theta_max: float
    ratio_min: float
    hip_drop: float
    still_score: float


class FallLogicFSM:
    def __init__(self, cfg: DetectionConfig, inference_fps: float):
        self.cfg = cfg
        self.infer_fps = inference_fps
        self.history: Deque[Features] = deque(maxlen=int(max(3, (cfg.T_pose_sec + cfg.T_still_sec + cfg.T_drop_sec) * inference_fps + 5)))
        self.cooldown_until: float = 0.0
        # Bridge A∧B to C with a grace window
        self.state: str = "idle"  # "idle" | "await_still"
        self.prelim_hist_len: int = 0
        self.still_deadline: float = 0.0
        self._pre_theta_max: float = 0.0
        self._pre_ratio_min: float = 1e9
        self._pre_hip_drop: float = 0.0

    @staticmethod
    def _center(a: Keypoint, b: Keypoint) -> Tuple[float, float]:
        return (0.5 * (a.x + b.x), 0.5 * (a.y + b.y))

    def _compute_features(self, pose: PoseResult) -> Optional[Features]:
        kps = pose.keypoints
        # MediaPipe indices for shoulders and hips
        # Left shoulder: 11, Right shoulder: 12, Left hip: 23, Right hip: 24 in MediaPipe Pose
        try:
            ls, rs, lh, rh = kps[11], kps[12], kps[23], kps[24]
        except IndexError:
            return None
        if min(ls.score, rs.score, lh.score, rh.score) < 0.2:
            return None

        scx, scy = self._center(ls, rs)
        hcx, hcy = self._center(lh, rh)
        vx, vy = (hcx - scx), (hcy - scy)
        # angle between body vector and vertical (0 deg = upright)
        angle = abs(math.degrees(math.atan2(vx, vy)))  # swap to measure from vertical

        # bounding rect height/width
        x, y, w, h = pose.bbox
        ratio = h / max(1.0, float(w))

        return Features(theta=angle, ratio=ratio, hip_y=hcy, h_person=h)

    def update(self, pose: Optional[PoseResult]) -> Tuple[bool, Optional[TriggerSnapshot]]:
        now = time.time()
        if now < self.cooldown_until:
            # still cooling down
            if pose is not None:
                ft = self._compute_features(pose)
                if ft:
                    self.history.append(ft)
            return (False, None)

        if pose is None:
            return (False, None)

        ft = self._compute_features(pose)
        if not ft:
            return (False, None)
        self.history.append(ft)

        cfg = self.cfg
        fps = max(self.infer_fps, 1.0)

        # A: posture sustained (theta>th or ratio<th) for T_pose
        n_pose = int(cfg.T_pose_sec * fps)
        A = False
        if len(self.history) >= n_pose:
            last = list(self.history)[-n_pose:]
            A = all((f.theta > cfg.angle_deg_th) or (f.ratio < cfg.ratio_th) for f in last)

        # B: hip drop within T_drop
        n_drop = int(cfg.T_drop_sec * fps)
        B = False
        hip_drop = 0.0
        if len(self.history) >= 2:
            # Consider any rapid drop within the last T_drop window.
            # Compute drop as current hip_y minus the minimum hip_y observed in the window (excluding current).
            window_len = max(2, min(len(self.history), n_drop + 1))
            window = list(self.history)[-window_len:]
            cur = window[-1]
            prior_min = min(f.hip_y for f in window[:-1])
            hip_drop = float(cur.hip_y - prior_min)
            B = hip_drop > cfg.hip_drop_px_th

        # D: min person height (current)
        D = self.history[-1].h_person >= cfg.min_person_height_px

        # Bridged FSM: when A∧B∧D is first observed, enter await_still; allow up to T_still + C_grace to satisfy stillness
        n_still = int(cfg.T_still_sec * fps)
        still_score = 999.0

        if self.state == "idle":
            if A and B and D:
                self.state = "await_still"
                self.prelim_hist_len = len(self.history)
                self.still_deadline = now + cfg.T_still_sec + getattr(cfg, "C_grace_sec", 0.6)
                self._pre_theta_max = max(f.theta for f in self.history)
                self._pre_ratio_min = min(f.ratio for f in self.history)
                self._pre_hip_drop = hip_drop
                return (False, None)
            return (False, None)

        if self.state == "await_still":
            since = len(self.history) - self.prelim_hist_len
            if since >= n_still:
                window = list(self.history)[-since:]
                seg = window[-n_still - 1 :]
                diffs = [abs(seg[i + 1].hip_y - seg[i].hip_y) for i in range(len(seg) - 1)]
                if diffs:
                    q80 = float(np.percentile(diffs, 80))
                    frac_ok = float(np.mean([d <= cfg.v_still_px_per_frame for d in diffs]))
                    still_score = q80
                    C = (q80 < cfg.v_still_px_per_frame * 1.2) and (frac_ok >= 0.7)
                else:
                    still_score = 0.0
                    C = True
                if C and D:
                    self.cooldown_until = now + cfg.cooldown_sec
                    snap = TriggerSnapshot(
                        theta_max=self._pre_theta_max,
                        ratio_min=self._pre_ratio_min,
                        hip_drop=self._pre_hip_drop,
                        still_score=still_score,
                    )
                    self.state = "idle"
                    return (True, snap)
            if now > self.still_deadline:
                self.state = "idle"
            return (False, None)

        return (False, None)
