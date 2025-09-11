from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .annotate import draw_pose, face_blur
from .config import PrivacyConfig, SaverConfig
from .pose import PoseResult
from .utils import (
    ensure_dir,
    event_dir,
    serialize_json,
    enforce_retention,
)


@dataclass
class FrameToSave:
    frame: np.ndarray
    t_rel_ms: int  # negative for pre, positive for post
    pose: Optional[PoseResult]


@dataclass
class CompletedEvent:
    event_id: str
    ts_utc: str
    camera_id: str
    frames: List[FrameToSave]  # includes both pre and post
    features: dict
    model: dict
    inference_fps: float
    base_dir: Path
    privacy: PrivacyConfig
    saver: SaverConfig
    host: str
    app_version: str
    git_commit: str = ""


class SaverWorker:
    def __init__(self) -> None:
        self.queue: List[CompletedEvent] = []
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.stop_flag = False
        self.thread = threading.Thread(target=self._run, name="saver_worker", daemon=True)
        self.thread.start()

    def submit(self, ev: CompletedEvent) -> None:
        with self.cv:
            self.queue.append(ev)
            self.cv.notify_all()

    def stop(self) -> None:
        with self.cv:
            self.stop_flag = True
            self.cv.notify_all()
        self.thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            with self.cv:
                while not self.queue and not self.stop_flag:
                    self.cv.wait(timeout=0.5)
                if self.stop_flag and not self.queue:
                    return
                ev = self.queue.pop(0)
            try:
                self._save_event(ev)
                logging.info("Event saved: %s", ev.event_id)
            except Exception as e:
                logging.exception("Event save failed: %s", getattr(ev, 'event_id', 'unknown'))

    def _save_event(self, ev: CompletedEvent) -> None:
        # enforce retention before saving
        try:
            enforce_retention(ev.base_dir, ev.privacy.retention_days, min_free_pct=5.0)
        except Exception:
            pass
        ts = dt.datetime.strptime(ev.ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        out_dir = event_dir(ev.base_dir, ev.camera_id, ev.event_id, ts)
        ensure_dir(out_dir)

        # Save frames
        img_ext = ".jpg" if ev.saver.image_format.lower() == "jpg" else ".png"
        params = [cv2.IMWRITE_JPEG_QUALITY, int(ev.saver.jpeg_quality)] if img_ext == ".jpg" else []

        saved_files = []
        for fr in ev.frames:
            img = fr.frame
            if ev.privacy.face_blur:
                img = face_blur(img, kernel=ev.privacy.blur_kernel)
            img_anno = img
            if ev.saver.save_annotated and fr.pose is not None:
                img_anno = draw_pose(img, fr.pose)
            # filenames
            fname_anno = f"annotated_{fr.t_rel_ms}.jpg" if img_ext == ".jpg" else f"annotated_{fr.t_rel_ms}.png"
            fpath_anno = out_dir / fname_anno
            cv2.imwrite(str(fpath_anno), img_anno, params)
            saved_files.append({"file": fpath_anno.name, "kind": "annotated", "t_rel_ms": fr.t_rel_ms})
            if ev.saver.save_raw:
                fname_raw = f"raw_{fr.t_rel_ms}.jpg" if img_ext == ".jpg" else f"raw_{fr.t_rel_ms}.png"
                fpath_raw = out_dir / fname_raw
                cv2.imwrite(str(fpath_raw), img, params)
                saved_files.append({"file": fpath_raw.name, "kind": "raw", "t_rel_ms": fr.t_rel_ms})

        # Optional: save clip
        if ev.saver.video_clip.enabled and len(ev.frames) > 1:
            fps = ev.saver.video_clip.fps
            h, w = ev.frames[0].frame.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*ev.saver.video_clip.codec)
            clip_path = out_dir / "clip.mp4"
            writer = cv2.VideoWriter(str(clip_path), fourcc, fps, (w, h))
            for fr in ev.frames:
                writer.write(fr.frame)
            writer.release()
            logging.info("Clip saved: %s", clip_path)

        # event.json
        event_json = {
            "event_id": ev.event_id,
            "camera_id": ev.camera_id,
            "timestamp_utc": ev.ts_utc,
            "model": ev.model,
            "decision": ev.features,
            "track_id": 0,
            "frames": {
                "pre_ms": int(ev.saver.pre_seconds * 1000),
                "post_ms": int(ev.saver.post_seconds * 1000),
                "inference_fps": ev.inference_fps,
                "saved_files": saved_files,
            },
            "privacy": {
                "face_blur": ev.privacy.face_blur,
                "blur_kernel": ev.privacy.blur_kernel,
                "redact_metadata": ev.privacy.redact_metadata,
            },
            "system": {
                "host": ev.host,
                "app_version": ev.app_version,
                "git_commit": ev.git_commit,
            },
        }
        meta_path = out_dir / "event.json"
        meta_path.write_text(
            serialize_json(event_json, redact=ev.privacy.redact_metadata), encoding="utf-8"
        )
        logging.info("Metadata saved: %s", meta_path)
