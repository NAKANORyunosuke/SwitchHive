from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

import cv2

from .config import CameraConfig
from .utils import iso_utc, utc_now


@dataclass
class FrameRecord:
    ts_utc: str
    frame: any  # numpy.ndarray
    index: int


class CaptureThread:
    def __init__(self, cfg: CameraConfig, ring_seconds: float = 6.0) -> None:
        self.cfg = cfg
        self.ring_seconds = ring_seconds
        self.cap: Optional[cv2.VideoCapture] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        # up to ring_seconds * fps frames
        self.ring: Deque[FrameRecord] = deque(maxlen=int(cfg.fps * ring_seconds))
        self._index = 0

    def start(self) -> None:
        src = self.cfg.source
        # Accept both numeric (int) and string values (e.g., "0", "rtsp://...", file path)
        if isinstance(src, int):
            src_any: any = src
        elif isinstance(src, str) and src.strip().isdigit():
            src_any = int(src.strip())
        else:
            src_any = src
        cap = cv2.VideoCapture(src_any)
        if self.cfg.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        if self.cfg.height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        if self.cfg.fps:
            cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)

        if not cap.isOpened():
            raise RuntimeError(f"Failed to open camera source: {src}")

        self.cap = cap
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, name="capture_thread", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        assert self.cap is not None
        cap = self.cap
        target_delay = 1.0 / max(self.cfg.fps, 1)
        while not self.stop_flag.is_set():
            start = time.time()
            ok, frame = cap.read()
            if not ok:
                # brief backoff and retry
                time.sleep(0.3)
                continue
            self._index += 1
            self.ring.append(
                FrameRecord(ts_utc=iso_utc(utc_now()), frame=frame, index=self._index)
            )
            elapsed = time.time() - start
            delay = max(0.0, target_delay - elapsed)
            if delay > 0:
                time.sleep(delay)

    def latest(self) -> Optional[FrameRecord]:
        try:
            return self.ring[-1]
        except IndexError:
            return None

    def stop(self) -> None:
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None
        if self.cap:
            self.cap.release()
            self.cap = None
