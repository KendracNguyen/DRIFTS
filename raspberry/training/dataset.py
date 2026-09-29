"""PyTorch Dataset loader for Simuletic DMS Dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

try:
    from PIL import Image
    from torch.utils.data import Dataset
except ImportError:
    # Allow importing for inspection even without torch installed
    class Dataset:
        pass
    Image = None

CLASS_MAP = {
    "open": 0,
    "closed": 1,
    "drowsy/microsleep": 2,
    "drowsy-microsleep": 2,
    "microsleep": 2,
}

CLASS_NAMES = ["Open", "Closed", "Drowsy/Microsleep"]


class SimuleticDMSDataset(Dataset):
    """Parses Simuletic_DMS_Dataset JSON annotations and pairs with frames."""

    def __init__(
        self,
        dataset_root: str | Path,
        split: str = "train",
        val_sequences: Optional[list[str]] = None,
        transform: Optional[Callable] = None,
    ):
        self.root = Path(dataset_root)
        self.images_dir = self.root / "images"
        self.labels_dir = self.root / "labels"
        self.split = split
        self.transform = transform

        if val_sequences is None:
            # Hold out one normal and one sleep sequence for validation
            self.val_sequences = {"driver_full_sleep4.json", "driver_microsleep2.json"}
        else:
            self.val_sequences = set(val_sequences)

        self.samples = []
        self._load_dataset()

    def _load_dataset(self) -> None:
        if not self.labels_dir.exists():
            raise FileNotFoundError(f"Labels directory not found: {self.labels_dir}")

        json_files = sorted(self.labels_dir.glob("*.json"))
        for json_file in json_files:
            is_val = json_file.name in self.val_sequences
            if (self.split == "val" and not is_val) or (self.split == "train" and is_val):
                continue

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for sample in data.get("samples", []):
                img_name = sample.get("image")
                img_path = self.images_dir / img_name
                if not img_path.exists():
                    continue

                attrs = sample.get("attributes", {})
                raw_state = str(attrs.get("eye_state", "")).strip().lower()
                class_id = CLASS_MAP.get(raw_state, None)
                if class_id is None:
                    continue

                self.samples.append({
                    "image_path": img_path,
                    "class_id": class_id,
                    "eye_state": attrs.get("eye_state"),
                    "perclos": attrs.get("perclos", 0.0),
                    "head_pose": attrs.get("head_pose", {}),
                    "zone": attrs.get("zone", ""),
                    "timestamp": sample.get("timestamp", 0.0),
                    "sequence": json_file.name,
                })

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        item = self.samples[idx]
        image = Image.open(item["image_path"]).convert("RGB")

        if self.transform:
            image = self.transform(image)

        return image, item["class_id"], item


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent.parent
    ds_root = repo_root / "Simuletic_DMS_Dataset"
    if not ds_root.exists():
        ds_root = Path(__file__).resolve().parent.parent / "Simuletic_DMS_Dataset"
    train_ds = SimuleticDMSDataset(ds_root, split="train")
    val_ds = SimuleticDMSDataset(ds_root, split="val")
    print(f"Loaded Simuletic DMS Dataset from {ds_root}:")
    print(f"  Train samples: {len(train_ds)}")
    print(f"  Val samples:   {len(val_ds)}")
