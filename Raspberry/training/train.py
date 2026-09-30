"""Train MobileNetV2 classifier on the Simuletic DMS Dataset.

Usage (on GPU machine or Google Colab):
    pip install -r requirements_training.txt
    python train.py --epochs 30 --batch-size 16
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader
    from torchvision import models, transforms
except ImportError:
    print("[ERROR] PyTorch and torchvision are required for training.")
    print("Install with: pip install -r requirements_training.txt")

from dataset import SimuleticDMSDataset, CLASS_NAMES


def get_transforms(img_size: int = 224):
    train_transform = transforms.Compose([
        transforms.Resize((img_size, img_size)),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.ColorJitter(brightness=0.2, contrast=0.2),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    val_transform = transforms.Compose([
        transforms.Resize((img_size, img_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    return train_transform, val_transform


def train(args):
    device = torch.device(args.device if torch.cuda.is_available() and args.device != "cpu" else "cpu")
    print(f"Using device: {device}")

    # 1. Dataset & DataLoaders
    train_tf, val_tf = get_transforms(args.img_size)
    data_root = Path(args.data_dir)

    train_ds = SimuleticDMSDataset(data_root, split="train", transform=train_tf)
    val_ds = SimuleticDMSDataset(data_root, split="val", transform=val_tf)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=args.workers)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers)

    print(f"Dataset loaded: {len(train_ds)} train samples, {len(val_ds)} val samples.")

    # 2. Model: MobileNetV2 with 3 classes
    weights = models.MobileNet_V2_Weights.DEFAULT
    model = models.mobilenet_v2(weights=weights)

    # Replace classifier head for 3 classes: Open, Closed, Drowsy/Microsleep
    num_ftrs = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(p=0.2),
        nn.Linear(num_ftrs, len(CLASS_NAMES)),
    )
    model.to(device)

    # 3. Loss & Optimizer
    # Optional class weighting to balance classes
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    best_val_acc = 0.0
    best_model_path = output_dir / "best_model.pth"

    print("\nStarting training...")
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        train_correct = 0
        total_train = 0

        for imgs, labels, _ in train_loader:
            imgs, labels = imgs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(imgs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            train_loss += loss.item() * imgs.size(0)
            _, preds = torch.max(outputs, 1)
            train_correct += torch.sum(preds == labels.data).item()
            total_train += imgs.size(0)

        scheduler.step()

        # Validation
        model.eval()
        val_loss = 0.0
        val_correct = 0
        total_val = 0

        with torch.no_grad():
            for imgs, labels, _ in val_loader:
                imgs, labels = imgs.to(device), labels.to(device)
                outputs = model(imgs)
                loss = criterion(outputs, labels)
                val_loss += loss.item() * imgs.size(0)
                _, preds = torch.max(outputs, 1)
                val_correct += torch.sum(preds == labels.data).item()
                total_val += imgs.size(0)

        epoch_train_loss = train_loss / total_train if total_train > 0 else 0
        epoch_train_acc = train_correct / total_train if total_train > 0 else 0
        epoch_val_loss = val_loss / total_val if total_val > 0 else 0
        epoch_val_acc = val_correct / total_val if total_val > 0 else 0

        print(
            f"Epoch {epoch:02d}/{args.epochs:02d} | "
            f"Train Loss: {epoch_train_loss:.4f} Acc: {epoch_train_acc:.4f} | "
            f"Val Loss: {epoch_val_loss:.4f} Acc: {epoch_val_acc:.4f}"
        )

        if epoch_val_acc >= best_val_acc:
            best_val_acc = epoch_val_acc
            torch.save(model.state_dict(), best_model_path)
            print(f"  --> Saved new best model to {best_model_path} (Val Acc: {best_val_acc:.4f})")

    print(f"\nTraining complete. Best Validation Accuracy: {best_val_acc:.4f}")
    print(f"Weights saved at: {best_model_path}")
    print("Next step: Run `python export_ncnn.py` to export for the Pi Zero 2 W.")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent.parent
    ds_default = repo_root / "Simuletic_DMS_Dataset"
    if not ds_default.exists():
        ds_default = Path(__file__).resolve().parent.parent / "Simuletic_DMS_Dataset"
    parser = argparse.ArgumentParser(description="Train MobileNetV2 on Simuletic DMS")
    parser.add_argument("--data-dir", default=str(ds_default), help="Path to dataset root")
    parser.add_argument("--output-dir", default="runs", help="Output directory for weights")
    parser.add_argument("--epochs", type=int, default=30, help="Number of epochs")
    parser.add_argument("--batch-size", type=int, default=16, help="Batch size")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate")
    parser.add_argument("--img-size", type=int, default=224, help="Input resolution")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    parser.add_argument("--workers", type=int, default=0, help="Dataloader workers")

    parsed_args = parser.parse_args()
    try:
        train(parsed_args)
    except NameError:
        sys.exit(1)
