# HokudaiTech Fall Detector

Lightweight, CPU-only fall detection pipeline based on the provided design doc `設計書.md`.

Pipeline: Camera → Capture → Preprocess → Pose Estimator (MediaPipe) → Fall Logic FSM → Event Queue → Image Saver

## Quick Start

1) Install dependencies (recommend venv):

```
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

2) Copy and edit config:

```
cp config.example.yaml config.yaml
```

3) Run:

```
python -m hokudai_fall --config config.yaml
```

## Notes

- Default pose backend uses MediaPipe (CPU). You can later add TFLite/ONNX backends by implementing `hokudai_fall/pose_backends/` adapters.
- Saving annotated frames and event.json follows the spec. Video clip saving is supported with OpenCV VideoWriter.
- Face blur uses OpenCV Haar Cascade.
- Disk retention is basic: removes events older than `retention_days`. If disk is critically low (< 5%), it removes oldest events regardless of age.

## License

See `LICENSE`.

