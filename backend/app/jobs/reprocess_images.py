"""
Retries inference for images stuck in pending (or failed, with the flag).
Schedule this to run regularly, e.g. every 15 minutes from cron or a
Railway cron service, until a proper job queue replaces background tasks.

Usage, from the backend directory:
    python -m app.jobs.reprocess_images
    python -m app.jobs.reprocess_images --include-failed --limit 500
"""

import argparse
import logging

from app.core.logging import configure_logging
from app.services.inference import reprocess_images

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-failed", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    configure_logging()
    count = reprocess_images(include_failed=args.include_failed, limit=args.limit)
    logging.getLogger(__name__).info("Reprocessed %d image(s).", count)
