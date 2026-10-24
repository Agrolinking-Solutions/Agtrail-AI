"""
Takes a farm out of the live tables without destroying it.

trash_farm copies the farm and everything attached to it (crop cycles, operations, satellite
readings, rule checks, visits, photos, predictions, sync records) into one TrashedRecord, then
removes those rows from the live tables. restore_record puts every row back as it was.
export_record gives the whole record as plain JSON to show the team.

Everything is found by following the database's own foreign keys, so a table added later is
included without changing this file. The farmer is removed only when this was their only farm;
otherwise they stay, because other farms still belong to them.
"""

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum

from geoalchemy2 import Geography, Geometry, WKTElement
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.farm import Farm
from app.models.trash import TrashedRecord

FARMER_KEY = "farmers"


def _tables():
    return Base.metadata.sorted_tables


def _is_shape(column) -> bool:
    return isinstance(column.type, (Geometry, Geography))


def _select_all(table):
    columns = [func.ST_AsText(c).label(c.name) if _is_shape(c) else c for c in table.c]
    return select(*columns)


def _pk_key(table, row: dict) -> tuple:
    return tuple(str(row[c.name]) for c in table.primary_key.columns)


def _plain(value):
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, PyEnum):
        return value.value if isinstance(value.value, (str, int)) else value.name
    return value


def _jsonable(rows: list[dict]) -> list[dict]:
    return json.loads(json.dumps(rows, default=_plain))


def _collect(db: Session, farm: Farm) -> dict[str, list[dict]]:
    """Every row that belongs to the farm, by table, found by following foreign keys downward."""
    farms = _tables()[[t.name for t in _tables()].index("farms")]
    collected: dict[str, dict[tuple, dict]] = {}
    row = db.execute(_select_all(farms).where(farms.c.id == farm.id)).mappings().one()
    collected["farms"] = {_pk_key(farms, dict(row)): dict(row)}

    changed = True
    while changed:
        changed = False
        for table in _tables():
            for fk in table.foreign_keys:
                parent_name = fk.column.table.name
                if parent_name not in collected or table.name == parent_name:
                    continue
                values = {r[fk.column.name] for r in collected[parent_name].values() if r.get(fk.column.name) is not None}
                if not values:
                    continue
                found = db.execute(_select_all(table).where(table.c[fk.parent.name].in_(values))).mappings().all()
                bucket = collected.setdefault(table.name, {})
                for found_row in found:
                    found_row = dict(found_row)
                    key = _pk_key(table, found_row)
                    if key not in bucket:
                        bucket[key] = found_row
                        changed = True
    return {name: list(rows.values()) for name, rows in collected.items()}


def describe(db: Session, agtrail_farm_id: str) -> dict:
    """What trashing would move, without moving anything."""
    farm = db.execute(select(Farm).where(Farm.agtrail_farm_id == agtrail_farm_id)).scalar_one_or_none()
    if farm is None:
        return {"found": False, "agtrail_farm_id": agtrail_farm_id}
    rows = _collect(db, farm)
    return {"found": True, "agtrail_farm_id": agtrail_farm_id, "farm_id": str(farm.id), "name": farm.name,
            "rows": {name: len(items) for name, items in rows.items()}}


def trash_farm(db: Session, agtrail_farm_id: str, reason: str) -> TrashedRecord:
    farm = db.execute(select(Farm).where(Farm.agtrail_farm_id == agtrail_farm_id)).scalar_one_or_none()
    if farm is None:
        raise LookupError(f"No farm with AgTrail id {agtrail_farm_id!r}.")

    rows = _collect(db, farm)
    farmers = _tables()[[t.name for t in _tables()].index("farmers")]
    farmer_row = db.execute(_select_all(farmers).where(farmers.c.id == farm.farmer_id)).mappings().one_or_none()
    others = db.execute(select(func.count()).select_from(Farm).where(Farm.farmer_id == farm.farmer_id, Farm.id != farm.id)).scalar()
    farmer_removed = farmer_row is not None and others == 0
    payload = {"tables": _jsonable_tables(rows), "farmer": _jsonable([dict(farmer_row)])[0] if farmer_row else None, "farmer_removed": farmer_removed}

    record = TrashedRecord(
        entity_type="farm", entity_id=agtrail_farm_id, label=f"{farm.name} ({agtrail_farm_id})", reason=reason, payload=payload,
    )
    db.add(record)
    db.flush()

    # Children first, so no row is deleted while another still points at it.
    for table in reversed(_tables()):
        for row in rows.get(table.name, []):
            db.execute(delete(table).where(*[table.c[c.name] == row[c.name] for c in table.primary_key.columns]))
    if farmer_removed:
        db.execute(delete(farmers).where(farmers.c.id == farmer_row["id"]))
    db.flush()
    return record


def _jsonable_tables(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {name: _jsonable(items) for name, items in rows.items()}


def _typed(column, value):
    if value is None:
        return None
    if _is_shape(column):
        return WKTElement(value, srid=column.type.srid if column.type.srid and column.type.srid > 0 else 4326)
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is uuid.UUID:
        return uuid.UUID(value)
    if python_type is datetime:
        return datetime.fromisoformat(value)
    if python_type is date:
        return date.fromisoformat(value)
    if python_type is Decimal:
        return Decimal(value)
    return value


def restore_record(db: Session, record_id: uuid.UUID) -> dict:
    record = db.get(TrashedRecord, record_id)
    if record is None:
        raise LookupError(f"No trash record {record_id}.")
    if record.restored_at is not None:
        raise ValueError(f"Trash record {record_id} was already restored on {record.restored_at.isoformat()}.")

    by_name = {t.name: t for t in _tables()}
    farmer = record.payload.get("farmer")
    restored = {}
    if record.payload.get("farmer_removed") and farmer:
        table = by_name["farmers"]
        db.execute(table.insert().values(**{c.name: _typed(c, farmer.get(c.name)) for c in table.c}))
        restored["farmers"] = 1
    for table in _tables():  # parents first
        items = record.payload["tables"].get(table.name, [])
        for item in items:
            db.execute(table.insert().values(**{c.name: _typed(c, item.get(c.name)) for c in table.c}))
        if items:
            restored[table.name] = len(items)
    record.restored_at = datetime.now(timezone.utc)
    db.flush()
    return restored


def export_record(db: Session, record_id: uuid.UUID) -> dict:
    record = db.get(TrashedRecord, record_id)
    if record is None:
        raise LookupError(f"No trash record {record_id}.")
    return {
        "id": str(record.id), "entity_type": record.entity_type, "entity_id": record.entity_id, "label": record.label,
        "reason": record.reason, "trashed_at": record.trashed_at.isoformat() if record.trashed_at else None,
        "restored_at": record.restored_at.isoformat() if record.restored_at else None, "payload": record.payload,
    }
