from sqlalchemy.orm import Session

from app.models import StoreSetting

PAUSED = "paused"


def is_paused(db: Session) -> bool:
    setting = db.get(StoreSetting, PAUSED)
    return setting is not None and setting.value == "1"


def set_paused(db: Session, paused: bool) -> None:
    setting = db.get(StoreSetting, PAUSED) or StoreSetting(key=PAUSED, value="0")
    setting.value = "1" if paused else "0"
    db.add(setting)
