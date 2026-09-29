"""
Trains the farm stage image classifier.

Reads labeled images from ml/datasets/raw/<stage_name>/, where each
subfolder name is itself the label, matching the FarmStage values used
in the database. Fine-tunes a pretrained EfficientNet on top of that
data and logs the run to MLflow.

This is intentionally simple to start. It will produce a poor model
until real labeled data exists in meaningful volume, its job right now
is to prove the training pipeline itself works end to end.
"""

import json
import argparse
import os
import random
from collections import Counter
from pathlib import Path

import mlflow
import mlflow.pytorch
import torch
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, models, transforms

STAGE_CLASSES = [
    "land_preparation",
    "planting",
    "fertilizer_application",
    "irrigation",
    "weeding",
    "pest_control",
    "pruning",
    "harvesting",
]

DATA_DIR = Path(__file__).resolve().parents[3] / "ml" / "datasets" / "raw"
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
NORMALIZE = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])


def build_transforms():
    # Must match the transform used for inference in app/services/inference.py.
    return transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        NORMALIZE,
    ])


def build_train_transforms():
    # Random variation stretches a small dataset further. Only applied to
    # the training split, validation uses the plain inference transform.
    return transforms.Compose([
        transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
        transforms.ToTensor(),
        NORMALIZE,
    ])


def stratified_split(targets: list[int], val_fraction: float, seed: int) -> tuple[list[int], list[int]]:
    """
    Splits indices so each class keeps roughly the same share in the
    validation set. Classes with a single image go to training only.
    """
    rng = random.Random(seed)
    by_class: dict[int, list[int]] = {}
    for index, target in enumerate(targets):
        by_class.setdefault(target, []).append(index)

    train_indices, val_indices = [], []
    for indices in by_class.values():
        rng.shuffle(indices)
        val_count = max(1, round(len(indices) * val_fraction)) if len(indices) > 1 else 0
        val_indices.extend(indices[:val_count])
        train_indices.extend(indices[val_count:])
    return train_indices, val_indices


def build_model(num_classes: int) -> nn.Module:
    model = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.DEFAULT)
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, num_classes)
    return model


def train(epochs: int, batch_size: int, learning_rate: float, seed: int):
    random.seed(seed)
    torch.manual_seed(seed)

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment("farm-stage-classifier")

    image_extensions = {".jpg", ".jpeg", ".png", ".ppm", ".bmp", ".pgm", ".tif", ".tiff", ".webp"}
    image_count = sum(
        1 for path in DATA_DIR.rglob("*") if path.suffix.lower() in image_extensions
    )

    if image_count == 0:
        print(f"No images found under {DATA_DIR}. Add labeled images to each "
              f"stage subfolder before training a real model.")
        return

    # Two views of the same folder, so training and validation images get
    # different transforms while sharing one index split.
    dataset = datasets.ImageFolder(root=str(DATA_DIR), transform=build_transforms())
    train_view = datasets.ImageFolder(root=str(DATA_DIR), transform=build_train_transforms())

    unknown_classes = set(dataset.classes) - set(STAGE_CLASSES)
    if unknown_classes:
        raise SystemExit(f"Folders not matching a FarmStage value: {sorted(unknown_classes)}")

    class_counts = Counter(dataset.classes[t] for t in dataset.targets)
    print("Images per class:", dict(sorted(class_counts.items())))

    train_indices, val_indices = stratified_split(dataset.targets, val_fraction=0.2, seed=seed)
    train_set = Subset(train_view, train_indices)
    val_set = Subset(dataset, val_indices)

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(num_classes=len(dataset.classes)).to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    with mlflow.start_run():
        mlflow.log_params({
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "num_classes": len(dataset.classes),
            "total_images": len(dataset),
            "seed": seed,
            "classes": ",".join(dataset.classes),
        })

        for epoch in range(epochs):
            model.train()
            running_loss = 0.0

            for images, labels in train_loader:
                images, labels = images.to(device), labels.to(device)

                optimizer.zero_grad()
                outputs = model(images)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()

                running_loss += loss.item() * images.size(0)

            avg_train_loss = running_loss / len(train_set)

            model.eval()
            correct = 0
            with torch.no_grad():
                for images, labels in val_loader:
                    images, labels = images.to(device), labels.to(device)
                    outputs = model(images)
                    predicted = outputs.argmax(dim=1)
                    correct += (predicted == labels).sum().item()

            val_accuracy = correct / len(val_set) if len(val_set) > 0 else 0.0

            mlflow.log_metrics({
                "train_loss": avg_train_loss,
                "val_accuracy": val_accuracy,
            }, step=epoch)

            print(f"Epoch {epoch + 1}/{epochs} | train_loss: {avg_train_loss:.4f} "
                  f"| val_accuracy: {val_accuracy:.4f}")

        mlflow.pytorch.log_model(model, "model")

        model_export_dir = Path(__file__).resolve().parent.parent / "models"
        model_export_dir.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), model_export_dir / "farm_stage_classifier.pt")
        with open(model_export_dir / "farm_stage_classifier_classes.json", "w") as f:
            json.dump(dataset.classes, f)
        print(f"Exported model weights and class list to {model_export_dir}")

    print("Training run complete, check the MLflow UI for results.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    train(epochs=args.epochs, batch_size=args.batch_size, learning_rate=args.learning_rate, seed=args.seed)