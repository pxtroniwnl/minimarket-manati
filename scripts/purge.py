"""Borra los mensajes viejos ahora mismo [J2]. El servidor ya lo hace en cada arranque.

    uv run python -m scripts.purge          # usa RETENTION_DAYS (90)
    uv run python -m scripts.purge 30       # o los días que digas
"""

import sys

from app.db import SessionLocal
from app.services.retention import purge_old_messages


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with SessionLocal() as db:
        print(f"Mensajes borrados: {purge_old_messages(db, days)}")


if __name__ == "__main__":
    main()
