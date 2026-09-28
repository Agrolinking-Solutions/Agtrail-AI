import hashlib
import json
import logging
import threading
from pathlib import Path

import httpx
import torch
from PIL import UnidentifiedImageError
from sqlalchemy import select
from torchvision import models, transforms

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.image import FarmImage, ImageStatus
from app.models.prediction import Prediction, FarmStage
from app.services.safe_fetch import ImageTooLargeError, UnsafeURLError, fetch_image

logger = logging.getLogger(__name__)

MODEL_DIR = Path(settings.MODEL_PATH)
MODEL_WEIGHTS_PATH = MODEL_DIR / "farm_stage_classifier.pt"
MODEL_CLASSES_PATH = MODEL_DIR / "farm_stage_classifier_classes.json"

_model_lock = threading.Lock()
_cached_model = None
_cached_model_version = None
_cached_classes = None

_transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def _build_model(num_classes: int):
    model = models.efficientnet_b0(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier[1] = torch.nn.Linear(in_features, num_classes)
    return model


def _file_version(path: Path) -> str:
    # A content hash identifies the exact weights a prediction came from,
    # unlike the file's modified time, which changes on every checkout.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest[:16]}"


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

    with _model_lock:
        if _cached_model is not None:
            return _cached_model, _cached_classes, _cached_model_version

        if not MODEL_WEIGHTS_PATH.exists() or not MODEL_CLASSES_PATH.exists():
            return None, None, None

        with open(MODEL_CLASSES_PATH) as f:
            classes = json.load(f)

        unknown_classes = set(classes) - {stage.value for stage in FarmStage}
        if unknown_classes:
            raise ValueError(f"Model classes not in FarmStage: {sorted(unknown_classes)}")

        model = _build_model(num_classes=len(classes))
        # weights_only=True refuses to unpickle arbitrary objects, so a
        # tampered weights file can't run code when it's loaded.
        state_dict = torch.load(MODEL_WEIGHTS_PATH, map_location="cpu", weights_only=True)
        model.load_state_dict(state_dict)
        model.eval()

        _cached_model = model
        _cached_classes = classes
        _cached_model_version = _file_version(MODEL_WEIGHTS_PATH)
        return model, classes, _cached_model_version


def run_inference_for_image(image_id):
    """
    Runs the trained farm stage classifier against a single stored
    image, and writes the result as a new Prediction row.
    """
    db = SessionLocal()
    try:
        image = db.get(FarmImage, image_id)
        if image is None:
            logger.warning("Image %s not found, skipping.", image_id)
            return

        model, classes, model_version = _load_latest_model()
        if model is None:
            logger.warning("No trained model available yet, leaving image %s pending.", image_id)
            return

        try:
            pil_image = fetch_image(image.storage_url)
        except (UnsafeURLError, ImageTooLargeError, UnidentifiedImageError) as exc:
            # Permanent problems with the image itself, retrying won't help.
            logger.warning("Rejected image %s: %s", image_id, exc)
            image.status = ImageStatus.failed
            db.commit()
            return
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Could not load image %s: %s", image_id, exc)
            image.status = ImageStatus.failed
            db.commit()
            return

        try:
            input_tensor = _transform(pil_image).unsqueeze(0)

            with torch.no_grad():
                outputs = model(input_tensor)
                probabilities = torch.softmax(outputs, dim=1)[0]
                predicted_index = int(torch.argmax(probabilities).item())
                confidence = float(probabilities[predicted_index].item())

            # Below the threshold the model is guessing, record it as
            # unknown rather than presenting a guess as a finding. The
            # full scores are still kept in raw_scores.
            if confidence >= settings.MIN_PREDICTION_CONFIDENCE:
                predicted_stage = FarmStage(classes[predicted_index])
            else:
                predicted_stage = FarmStage.unknown

            prediction = Prediction(
                image_id=image.id,
                farm_id=image.visit.farm_id,
                model_version=model_version,
                predicted_stage=predicted_stage,
                confidence=confidence,
                raw_scores={cls: float(probabilities[i]) for i, cls in enumerate(classes)},
            )
            db.add(prediction)

            image.status = ImageStatus.processed
            db.commit()

            logger.info(
                "Image %s predicted as %s (%.2f confidence).",
                image_id, predicted_stage.value, confidence,
            )
        except Exception:
            logger.exception("Prediction failed for image %s", image_id)
            db.rollback()
            image.status = ImageStatus.failed
            db.commit()
    finally:
        db.close()


def reprocess_images(include_failed: bool = False, limit: int = 100) -> int:
    """
    Runs inference for images left pending, for example because the
    process restarted before a background task ran, or no model existed
    when the image arrived. Returns the number of images attempted.
    """
    statuses = [ImageStatus.pending]
    if include_failed:
        statuses.append(ImageStatus.failed)

    with SessionLocal() as db:
        image_ids = db.execute(
            select(FarmImage.id).where(FarmImage.status.in_(statuses)).limit(limit)
        ).scalars().all()

    for image_id in image_ids:
        run_inference_for_image(image_id)
    return len(image_ids)
