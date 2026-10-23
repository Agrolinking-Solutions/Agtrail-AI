"""
Writes the JSON Schema copy of every contract model to backend/contracts/.

    python -m app.contracts.export_schemas

A test regenerates the schemas and compares them with the files on disk, so a
model change that forgets this step fails CI.
"""

import json
from pathlib import Path

from app.contracts.knowledge_base import CONTRACT_MODELS
from app.contracts.pis import PIS_CONTRACT_MODELS

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "knowledge_base"
PIS_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "pis"


def render_schemas() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in CONTRACT_MODELS.items()
    }


def render_pis_schemas() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in PIS_CONTRACT_MODELS.items()
    }


def main() -> None:
    for directory, schemas in ((CONTRACTS_DIR, render_schemas()), (PIS_CONTRACTS_DIR, render_pis_schemas())):
        directory.mkdir(parents=True, exist_ok=True)
        for filename, text in schemas.items():
            (directory / filename).write_text(text)
            print(f"wrote {directory / filename}")


if __name__ == "__main__":
    main()
