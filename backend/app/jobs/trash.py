"""
Move a farm to the trash instead of deleting it, and bring it back or show it later.

    python -m app.jobs.trash list
    python -m app.jobs.trash farm TEST-FARM-001                  (preview, nothing moves)
    python -m app.jobs.trash farm TEST-FARM-001 --yes --reason "test record"
    python -m app.jobs.trash export <trash id>                   (prints it as JSON)
    python -m app.jobs.trash restore <trash id> --yes

Without --yes the commands only say what they would do.
"""

import argparse
import json
import logging
import uuid

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.trash import TrashedRecord
from app.services import trash

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    farm = sub.add_parser("farm")
    farm.add_argument("agtrail_farm_id")
    farm.add_argument("--reason", default="Moved to the trash by hand")
    farm.add_argument("--yes", action="store_true")
    for name in ("export", "restore"):
        cmd = sub.add_parser(name)
        cmd.add_argument("trash_id")
        if name == "restore":
            cmd.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.command == "list":
            records = db.execute(select(TrashedRecord).order_by(TrashedRecord.trashed_at.desc())).scalars().all()
            for r in records:
                print(f"{r.id}  {r.trashed_at:%Y-%m-%d %H:%M}  {'restored' if r.restored_at else 'in trash':9}  {r.label}  | {r.reason}")
            print(f"{len(records)} record(s) in the trash.")
        elif args.command == "farm":
            info = trash.describe(db, args.agtrail_farm_id)
            if not info["found"]:
                print(f"No farm with AgTrail id {args.agtrail_farm_id!r}.")
                return 1
            print(f"{info['name']} ({args.agtrail_farm_id}) would move these rows: {info['rows']}")
            if not args.yes:
                print("Nothing was moved. Add --yes to do it.")
                return 0
            record = trash.trash_farm(db, args.agtrail_farm_id, args.reason)
            db.commit()
            print(f"Moved to the trash. Trash id: {record.id}")
        elif args.command == "export":
            print(json.dumps(trash.export_record(db, uuid.UUID(args.trash_id)), indent=2))
        elif args.command == "restore":
            if not args.yes:
                print("Nothing was restored. Add --yes to do it.")
                return 0
            restored = trash.restore_record(db, uuid.UUID(args.trash_id))
            db.commit()
            print(f"Restored: {restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
