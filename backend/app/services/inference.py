import io
import json
from pathlib import Path

import httpx
import torch
from PIL import Image
from torchvision import models

from app.core.database import SessionLocal
from app.models.image import FarmImage, ImageStatus
from app.models.prediction import Prediction, FarmStage

MODEL_DIR = Path(__file__).resolve().parents[2] / "ml" / "models"
MODEL_WEIGHTS_PATH = MODEL_DIR / "farm_stage_classifier.pt"
MODEL_CLASSES_PATH = MODEL_DIR / "farm_stage_classifier_classes.json"

_cached_model = None
_cached_model_version = None
_cached_classes = None


def _build_model(num_classes: int):
    model = models.efficientnet_b0(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier[1] = torch.nn.Linear(in_features, num_classes)
    return model


def _load_latest_model():
    """
    Loads the trained model directly from the file exported by the
    training script, alongside the exact class order it was trained
    with. This runs entirely offline, no connection to a tracking
    server needed, so it works the same whether running locally or
    on a deployed server.

    Returns (model, classes, version_label), or (None, None, None) if
    no exported model exists yet.
    """
    global _cached_model, _cached_model_version, _cached_classes

    if _cached_model is not None:
        return _cached_model, _cached_classes, _cached_model_version

    if not MODEL_WEIGHTS_PATH.exists() or not MODEL_CLASSES_PATH.exists():
        return None, None, None

    with open(MODEL_CLASSES_PATH) as f:
        classes = json.load(f)

    model = _build_model(num_classes=len(classes))
    state_dict = torch.load(MODEL_WEIGHTS_PATH, map_location="cpu")
    model.load_state_dict(state_dict)
    model.eval()

    _cached_model = model
    _cached_classes = classes
    _cached_model_version = f"file:{MODEL_WEIGHTS_PATH.stat().st_mtime_ns}"
    return model, classes, _cached_model_version


def _build_transform():
    from torchvision import transforms
    return transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


def run_inference_for_image(image_id):
    """
    Runs the trained farm stage classifier against a single stored
    image, and writes the result as a new Prediction row.
    """
    db = SessionLocal()
    try:
        image = db.get(FarmImage, image_id)
        if image is None:
            print(f"[inference] Image {image_id} not found, skipping.")
            return

        model, classes, model_version = _load_latest_model()
        if model is None:
            print(f"[inference] No trained model available yet, leaving image {image_id} pending.")
            return

        try:
            response = httpx.get(
                image.storage_url,
                headers={"User-Agent": "AgtrailInferenceService/1.0 (contact: fashjohn04@gmail.com)"},
                timeout=10.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            pil_image = Image.open(io.BytesIO(response.content)).convert("RGB")

        except Exception as exc:
            print(f"[inference] Could not load image {image_id} from {image.storage_url}: {exc}")
            image.status = ImageStatus.failed
            db.commit()
            return

        try:
            transform = _build_transform()
            input_tensor = transform(pil_image).unsqueeze(0)

            with torch.no_grad():
                outputs = model(input_tensor)
                probabilities = torch.softmax(outputs, dim=1)[0]
                predicted_index = int(torch.argmax(probabilities).item())
                confidence = float(probabilities[predicted_index].item())

            predicted_stage = classes[predicted_index]

            prediction = Prediction(
                image_id=image.id,
                farm_id=image.visit.farm_id,
                model_version=model_version,
                predicted_stage=FarmStage(predicted_stage),
                confidence=confidence,
                raw_scores={cls: float(probabilities[i]) for i, cls in enumerate(classes)},
            )
            db.add(prediction)

            image.status = ImageStatus.processed
            db.commit()

            print(f"[inference] Image {image_id} predicted as {predicted_stage} ({confidence:.2f} confidence).")
        except Exception as exc:
            print(f"[inference] Prediction failed for image {image_id}: {exc!r}")
            image.status = ImageStatus.failed
            db.commit()
    finally:
        db.close()