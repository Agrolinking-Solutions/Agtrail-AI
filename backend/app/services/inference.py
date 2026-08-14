import io

import httpx
import mlflow
import mlflow.pytorch
import torch
from PIL import Image

from app.core.database import SessionLocal
from app.models.image import FarmImage, ImageStatus
from app.models.prediction import Prediction, FarmStage

MLFLOW_TRACKING_URI = "http://localhost:5000"
MLFLOW_EXPERIMENT_NAME = "farm-stage-classifier"
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

_cached_model = None
_cached_model_version = None


def _load_latest_model():
    """
    Loads the most recently trained model logged to MLflow, if one
    exists. Returns (model, version_label), or (None, None) if no
    trained model has been logged yet.
    """
    global _cached_model, _cached_model_version

    if _cached_model is not None:
        return _cached_model, _cached_model_version

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = mlflow.tracking.MlflowClient()

    experiment = client.get_experiment_by_name(MLFLOW_EXPERIMENT_NAME)
    if experiment is None:
        return None, None

    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["start_time DESC"],
        max_results=1,
    )
    if not runs:
        return None, None

    latest_run = runs[0]
    model_uri = f"runs:/{latest_run.info.run_id}/model"

    try:
        model = mlflow.pytorch.load_model(model_uri)
    except Exception:
        return None, None

    model.eval()
    _cached_model = model
    _cached_model_version = latest_run.info.run_id
    return model, latest_run.info.run_id


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

    Runs as a background task, opens its own database session rather
    than reusing the request's, since it executes after the webhook
    response has already been sent. Never raises, since a failure
    here should never crash the request that triggered it, it just
    logs and leaves the image in a state reflecting what happened.
    """
    db = SessionLocal()
    try:
        image = db.get(FarmImage, image_id)
        if image is None:
            print(f"[inference] Image {image_id} not found, skipping.")
            return

        model, model_version = _load_latest_model()
        if model is None:
            print(f"[inference] No trained model available yet, leaving image {image_id} pending.")
            return

        try:
            response = httpx.get(image.storage_url, timeout=10.0)
            response.raise_for_status()
            pil_image = Image.open(io.BytesIO(response.content)).convert("RGB")
        except Exception as exc:
            print(f"[inference] Could not load image {image_id} from {image.storage_url}: {exc}")
            image.status = ImageStatus.failed
            db.commit()
            return

        transform = _build_transform()
        input_tensor = transform(pil_image).unsqueeze(0)

        with torch.no_grad():
            outputs = model(input_tensor)
            probabilities = torch.softmax(outputs, dim=1)[0]
            predicted_index = int(torch.argmax(probabilities).item())
            confidence = float(probabilities[predicted_index].item())

        predicted_stage = STAGE_CLASSES[predicted_index]

        prediction = Prediction(
            image_id=image.id,
            farm_id=image.visit.farm_id,
            model_version=model_version,
            predicted_stage=FarmStage(predicted_stage),
            confidence=confidence,
            raw_scores={cls: float(probabilities[i]) for i, cls in enumerate(STAGE_CLASSES)},
        )
        db.add(prediction)

        image.status = ImageStatus.processed
        db.commit()

        print(f"[inference] Image {image_id} predicted as {predicted_stage} ({confidence:.2f} confidence).")
    finally:
        db.close()