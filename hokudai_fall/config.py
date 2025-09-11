from __future__ import annotations

import dataclasses as dc
import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dc.dataclass
class CameraConfig:
    source: str
    width: int = 1280
    height: int = 720
    fps: int = 30
    inference_fps: int = 12
    camera_id: str = "cam01"


@dc.dataclass
class ModelConfig:
    backend: str = "mediapipe"  # implemented: mediapipe; stubs: tflite/onnx/opencv-dnn
    model_path: str = ""
    num_threads: int = 2


@dc.dataclass
class DetectionConfig:
    min_conf_joints: int = 8
    angle_deg_th: float = 55.0
    ratio_th: float = 0.6
    T_pose_sec: float = 0.5
    hip_drop_px_th: int = 40
    T_drop_sec: float = 0.4
    T_still_sec: float = 1.0
    v_still_px_per_frame: float = 0.5
    min_person_height_px: int = 120
    cooldown_sec: float = 5.0
    C_grace_sec: float = 0.6


@dc.dataclass
class VideoClipConfig:
    enabled: bool = True
    fps: int = 15
    max_seconds: float = 6.0
    codec: str = "mp4v"


@dc.dataclass
class SaverConfig:
    base_dir: str = "./falls"
    save_annotated: bool = True
    save_raw: bool = False
    pre_seconds: float = 2.0
    post_seconds: float = 3.0
    image_format: str = "jpg"
    jpeg_quality: int = 90
    video_clip: VideoClipConfig = dc.field(default_factory=VideoClipConfig)


@dc.dataclass
class PrivacyConfig:
    face_blur: bool = True
    blur_kernel: int = 31
    encrypt_at_rest: bool = False
    retention_days: int = 30
    redact_metadata: bool = True


@dc.dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = ""
    export_prometheus: bool = False


@dc.dataclass
class AppConfig:
    camera: CameraConfig
    model: ModelConfig
    detection: DetectionConfig
    saver: SaverConfig
    privacy: PrivacyConfig
    logging: LoggingConfig

    @staticmethod
    def from_mapping(d: Dict[str, Any]) -> "AppConfig":
        return AppConfig(
            camera=CameraConfig(**d.get("camera", {})),
            model=ModelConfig(**d.get("model", {})),
            detection=DetectionConfig(**d.get("detection", {})),
            saver=SaverConfig(
                **{
                    k: v
                    for k, v in d.get("saver", {}).items()
                    if k != "video_clip"
                }
            ),
            privacy=PrivacyConfig(**d.get("privacy", {})),
            logging=LoggingConfig(**d.get("logging", {})),
        )


def load_config(path: Path) -> AppConfig:
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        # default YAML
        data = yaml.safe_load(text)

    cfg = AppConfig.from_mapping(data or {})

    # handle nested video_clip separately to use dataclass default when absent
    vc = (data or {}).get("saver", {}).get("video_clip", None)
    if vc is not None:
        cfg.saver.video_clip = VideoClipConfig(**vc)

    return cfg
