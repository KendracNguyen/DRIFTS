# MobileNetV2 Training Pipeline on Simuletic DMS Dataset

This directory contains the training and export pipeline for fine-tuning a lightweight MobileNetV2 classifier on the **Simuletic DMS Dataset**.

Unlike general bounding box models, the Simuletic DMS Dataset provides per-frame annotations for:
- `eye_state`: `Open`, `Closed`, `Drowsy/Microsleep`
- `perclos`: rolling eye-closure score
- `head_pose`: pitch, yaw, roll
- `zone`: gaze attention area

MobileNetV2 provides optimal performance (~5–10 FPS) on the quad-core ARM Cortex-A53 processor of the Raspberry Pi Zero 2 W with minimal memory consumption.

## Prerequisites

Training should be executed on a machine with an NVIDIA GPU, cloud VM, or Google Colab:

```bash
pip install -r requirements_training.txt
```

## Step 1: Train the Model

To train MobileNetV2 with cosine annealing and transfer learning from ImageNet:

```bash
python train.py --epochs 30 --batch-size 16 --lr 0.001
```

Validation checkpoints are evaluated after each epoch. The checkpoint with the highest validation accuracy is saved to `runs/best_model.pth`.

## Step 2: Export to ONNX and NCNN

Export the trained model to ONNX and convert to NCNN param/bin format:

```bash
python export_ncnn.py --weights runs/best_model.pth --output-dir ../model
```

If `onnx2ncnn` is not installed on your system, install it via:
```bash
# Ubuntu / Debian
sudo apt-get install libncnn-dev ncnn-tools
```
Or use the exported `mobilenetv2_dms.onnx` directly with ONNX Runtime.
