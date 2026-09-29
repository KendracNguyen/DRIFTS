"""Export trained MobileNetV2 PyTorch model to ONNX and NCNN format for Raspberry Pi.

Usage:
    python export_ncnn.py --weights runs/best_model.pth --output-dir ../model
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import torch
    import torch.nn as nn
    from torchvision import models
except ImportError:
    print("[ERROR] PyTorch and torchvision are required for export.")
    print("Install with: pip install -r requirements_training.txt")


def export(args):
    weights_path = Path(args.weights)
    if not weights_path.exists():
        print(f"[ERROR] Weights file not found: {weights_path}")
        print("Please train first using: python train.py")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading weights from {weights_path}...")
    model = models.mobilenet_v2()
    num_ftrs = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(p=0.2),
        nn.Linear(num_ftrs, 3),
    )

    state_dict = torch.load(weights_path, map_location="cpu")
    model.load_state_dict(state_dict)
    model.eval()

    # 1. Export to ONNX
    onnx_path = output_dir / "mobilenetv2_dms.onnx"
    dummy_input = torch.randn(1, 3, args.img_size, args.img_size)

    print(f"Exporting to ONNX: {onnx_path}...")
    torch.onnx.export(
        model,
        dummy_input,
        str(onnx_path),
        export_params=True,
        opset_version=12,
        do_constant_folding=True,
        input_names=["in0"],
        output_names=["out0"],
        dynamic_axes=None,  # Fixed shape optimal for NCNN conversion
    )
    print(f"[OK] ONNX model successfully saved to: {onnx_path}")

    # 2. Try converting ONNX to NCNN using onnx2ncnn if available
    param_path = output_dir / "mobilenetv2_dms.param"
    bin_path = output_dir / "mobilenetv2_dms.bin"

    onnx2ncnn_cmd = None
    for candidate in ["onnx2ncnn", "onnx2ncnn.exe"]:
        try:
            subprocess.run([candidate, "--version"], capture_output=True)
            onnx2ncnn_cmd = candidate
            break
        except FileNotFoundError:
            continue

    if onnx2ncnn_cmd:
        print(f"Converting ONNX to NCNN using {onnx2ncnn_cmd}...")
        cmd = [onnx2ncnn_cmd, str(onnx_path), str(param_path), str(bin_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[OK] NCNN model saved to {param_path} and {bin_path}")
        else:
            print(f"[WARN] onnx2ncnn failed: {res.stderr}")
    else:
        print("\nNote: 'onnx2ncnn' tool not found on PATH.")
        print("To generate .param and .bin files for NCNN on Raspberry Pi / Linux:")
        print(f"  onnx2ncnn {onnx_path} {param_path} {bin_path}")
        print("Or install ncnn tools via: sudo apt-get install libncnn-dev")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export MobileNetV2 to ONNX and NCNN")
    parser.add_argument("--weights", default="runs/best_model.pth", help="Path to best_model.pth")
    parser.add_argument("--output-dir", default="../model", help="Destination folder for exported models")
    parser.add_argument("--img-size", type=int, default=224, help="Input resolution (default: 224)")

    parsed_args = parser.parse_args()
    try:
        export(parsed_args)
    except NameError:
        sys.exit(1)
