from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque, List, Optional

import cv2
import numpy as np

from .capture import CaptureThread, FrameRecord
from .config import AppConfig, load_config
from .logic import FallLogicFSM
from .pose import PoseResult, build_estimator
from .saver import CompletedEvent, FrameToSave, SaverWorker
from .utils import event_id, host_name, iso_utc, utc_now
from .annotate import draw_pose, draw_hud_text


APP_VERSION = "0.9.0"


def setup_logging(level: str, file: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if file:
        try:
            path = Path(file)
            # If a directory is provided (or path ends with a separator), write app.log inside it
            if str(file).endswith(("/", "\\")) or path.is_dir():
                path = path / "app.log"
            # Ensure parent directory exists
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(path, encoding="utf-8")
            handlers.append(fh)
            print(f"Logging to file: {path}")
        except Exception as e:
            print(f"WARN: Failed to open log file '{file}': {e}. Falling back to stdout only.")
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fall Detector (Camera→Pose→Logic→Save)")
    p.add_argument("--config", type=str, default="config.yaml", help="Path to config YAML/JSON")
    p.add_argument("--display", action="store_true", help="Show preview window (debug)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    cfg_path = Path(args.config)
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        # Fallback to example if available
        ex = Path("config.example.yaml")
        if ex.exists():
            cfg = load_config(ex)
            print(f"WARN: Failed to load '{cfg_path}'. Using '{ex}'. Error: {e}")
        else:
            print(f"ERROR: Failed to load config '{cfg_path}': {e}")
            return 2
    setup_logging(cfg.logging.level, cfg.logging.file)

    logging.info("Starting Fall Detector %s (config=%s)", APP_VERSION, cfg_path)

    # Capture thread
    cap = CaptureThread(cfg.camera, ring_seconds=max(6.0, cfg.saver.pre_seconds + cfg.saver.post_seconds + 2))
    try:
        cap.start()
    except Exception as e:
        logging.error("Camera open failed for source '%s': %s", cfg.camera.source, e)
        logging.error("Please check camera.source in config (e.g., '0', RTSP URL, or video file)")
        return 3

    # Pose estimator
    try:
        estimator = build_estimator(cfg.model.backend)
    except SystemExit as e:
        logging.error("Model backend '%s' not implemented or failed to load (exit %s)", cfg.model.backend, e.code)
        return int(e.code or 101)
    except Exception:
        logging.exception("Failed to initialize model backend '%s'", cfg.model.backend)
        return 101

    # Logic FSM
    infer_fps = cfg.camera.inference_fps
    fsm = FallLogicFSM(cfg.detection, inference_fps=infer_fps)

    # Saver worker
    saver = SaverWorker()

    # Inference frame history (for pre/post collection)
    hist: Deque[tuple[FrameRecord, Optional[PoseResult]]] = deque(maxlen=int(cfg.saver.pre_seconds * infer_fps * 2 + 20))

    seq = 1
    collecting = None  # type: Optional[dict]
    next_infer_time = time.time()

    fps_counter = 0
    fps_t0 = time.time()
    fps_val = 0.0
    last_status = "idle"
    try:
        while True:
            lr = cap.latest()
            if lr is None:
                time.sleep(0.01)
                continue

            # keep inference rate
            now = time.time()
            if now < next_infer_time:
                time.sleep(max(0.0, next_infer_time - now))
            next_infer_time = time.time() + (1.0 / max(1, infer_fps))

            # Run pose estimation on latest frame
            pose = estimator.estimate(lr.frame)
            hist.append((lr, pose))
            fps_counter += 1
            if time.time() - fps_t0 >= 1.0:
                fps_val = fps_counter / (time.time() - fps_t0)
                fps_counter = 0
                fps_t0 = time.time()

            # If currently collecting post-frames, append and check completion
            if collecting is not None:
                collecting["frames"].append((lr, pose))
                if len(collecting["frames"]) >= collecting["need_post"]:
                    # finalize event and enqueue for saving
                    ev_frames: List[FrameToSave] = []
                    t0 = collecting["t0_ts"]
                    # pre frames (already in collecting["pre"]) + frames collected (includes the trigger frame as first)
                    for (fr, po) in collecting["pre"] + collecting["frames"]:
                        # compute relative ms from t0
                        t_rel_ms = int((fr.index - t0[1]) * (1000.0 / max(1, infer_fps)))
                        ev_frames.append(FrameToSave(frame=fr.frame, t_rel_ms=t_rel_ms, pose=po))

                    ev = CompletedEvent(
                        event_id=collecting["event_id"],
                        ts_utc=collecting["ts_utc"],
                        camera_id=cfg.camera.camera_id,
                        frames=ev_frames,
                        features=collecting["features"],
                        model={
                            "backend": cfg.model.backend,
                            "model_name": Path(cfg.model.model_path).name if cfg.model.model_path else "mediapipe_pose",
                            "model_version": "",
                            "num_threads": cfg.model.num_threads,
                        },
                        inference_fps=float(infer_fps),
                        base_dir=Path(cfg.saver.base_dir),
                        privacy=cfg.privacy,
                        saver=cfg.saver,
                        host=host_name(),
                        app_version=APP_VERSION,
                        git_commit=os.getenv("GIT_COMMIT", ""),
                    )
                    saver.submit(ev)
                    logging.info("Event queued: %s (frames=%d)", collecting["event_id"], len(ev_frames))
                    collecting = None

            # If not collecting, evaluate FSM for trigger
            if collecting is None and pose is not None:
                triggered, snap = fsm.update(pose)
                if triggered and snap is not None:
                    # build event
                    ev_id = event_id(cfg.camera.camera_id, seq)
                    seq += 1
                    ts_utc = lr.ts_utc
                    # collect pre-frames from history covering pre_seconds
                    need_pre = int(cfg.saver.pre_seconds * infer_fps)
                    pre_list = list(hist)[-need_pre:]
                    # post frames to collect
                    need_post = int(cfg.saver.post_seconds * infer_fps)
                    features = {
                        "angle_deg_th": float(cfg.detection.angle_deg_th),
                        "ratio_th": float(cfg.detection.ratio_th),
                        "hip_drop_px_th": int(cfg.detection.hip_drop_px_th),
                        "T_pose": float(cfg.detection.T_pose_sec),
                        "T_drop": float(cfg.detection.T_drop_sec),
                        "T_still": float(cfg.detection.T_still_sec),
                        "v_still": float(cfg.detection.v_still_px_per_frame),
                        "min_person_height_px": int(cfg.detection.min_person_height_px),
                        "cooldown_sec": float(cfg.detection.cooldown_sec),
                        "features_at_trigger": {
                            "theta_max": float(snap.theta_max),
                            "ratio_min": float(snap.ratio_min),
                            "hip_drop": float(snap.hip_drop),
                            "still_score": float(snap.still_score),
                        },
                    }
                    collecting = {
                        "event_id": ev_id,
                        "ts_utc": ts_utc,
                        "t0_ts": (ts_utc, lr.index),
                        "pre": pre_list,
                        "frames": [],
                        "need_post": need_post,
                        "features": features,
                    }
                    last_status = "TRIGGERED"
                    logging.info("Fall detected → start collecting (event %s)", ev_id)

            # Display (debug)
            if args.display:
                disp = lr.frame.copy()
                lines = []
                now_ts = time.time()
                cooldown_left = max(0.0, fsm.cooldown_until - now_ts)
                state = getattr(fsm, "state", "idle")
                still_left = max(0.0, getattr(fsm, "still_deadline", 0.0) - now_ts) if state == "await_still" else 0.0
                if pose is not None:
                    disp = draw_pose(disp, pose)
                    # compute quick features for HUD
                    try:
                        ft = fsm._compute_features(pose)  # type: ignore[attr-defined]
                    except Exception:
                        ft = None
                    # Hip drop and stillness over windows
                    B = False; hip_drop = 0.0
                    C = False; still_score = 0.0
                    A = False; D = False
                    if ft is not None:
                        # A
                        n_pose = int(cfg.detection.T_pose_sec * infer_fps)
                        if len(fsm.history) >= n_pose:
                            lastf = list(fsm.history)[-n_pose:]
                            A = all((f.theta > cfg.detection.angle_deg_th) or (f.ratio < cfg.detection.ratio_th) for f in lastf)
                        # B
                        n_drop = int(cfg.detection.T_drop_sec * infer_fps)
                        if len(fsm.history) >= 2:
                            window_len = max(2, min(len(fsm.history), n_drop + 1))
                            window = list(fsm.history)[-window_len:]
                            prior_min = min(f.hip_y for f in window[:-1])
                            hip_drop = float(window[-1].hip_y - prior_min)
                            B = hip_drop > cfg.detection.hip_drop_px_th
                        # C
                        n_still = int(cfg.detection.T_still_sec * infer_fps)
                        if len(fsm.history) >= n_still + 1:
                            seg = list(fsm.history)[-n_still - 1 :]
                            diffs = [abs(seg[i + 1].hip_y - seg[i].hip_y) for i in range(len(seg) - 1)]
                            if diffs:
                                q80 = float(np.percentile(diffs, 80))
                                frac_ok = float(np.mean([d <= cfg.detection.v_still_px_per_frame for d in diffs]))
                                still_score = q80
                                C = (q80 < cfg.detection.v_still_px_per_frame * 1.2) and (frac_ok >= 0.7)
                            else:
                                still_score = 0.0
                                C = True
                        # D
                        D = ft.h_person >= cfg.detection.min_person_height_px
                        lines += [
                            f"θ={ft.theta:.1f}°, r={ft.ratio:.2f}, hip_drop={hip_drop:.1f}px",
                            f"A={A} B={B} C={C} D={D} | state={state} cooldown={cooldown_left:.1f}s still_wait={still_left:.1f}s",
                        ]
                lines.insert(0, f"infer_fps={fps_val:.1f}")
                disp = draw_hud_text(disp, lines)
                cv2.imshow("FallDetector", disp)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        pass
    finally:
        try:
            saver.stop()
        except Exception:
            pass
        try:
            cap.stop()
        except Exception:
            pass
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
    return 0
