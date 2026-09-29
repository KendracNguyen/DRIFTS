# Trained Model Artifacts

This directory stores the trained MobileNetV2 classifier artifacts for inference on the Raspberry Pi Zero 2 W:

- `mobilenetv2_dms.param`: NCNN model structure/parameters
- `mobilenetv2_dms.bin`: NCNN quantized/FP32 weights binary
- `mobilenetv2_dms.onnx`: (Optional) ONNX format for cross-platform inference
- `labels.txt`: Class index mapping:
  - `0: Open` (Normal / Alert)
  - `1: Closed` (Drowsy)
  - `2: Drowsy/Microsleep` (Drowsy / Microsleep)

## How to Obtain or Export the Model

1. On a machine with GPU or cloud environment, run training:
   ```bash
   cd training
   pip install -r requirements_training.txt
   python train.py
   python export_ncnn.py
   ```
2. Copy `mobilenetv2_dms.param` and `mobilenetv2_dms.bin` (or `mobilenetv2_dms.onnx`) into this directory.
3. Deploy to the Pi using `deploy/deploy.ps1`.
