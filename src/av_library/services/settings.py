from sqlalchemy import select

from av_library.db.database import Database
from av_library.db.models import Setting


class SettingsService:
    def __init__(self, database: Database):
        self.database = database

    def update_interval_hours(self) -> int:
        with self.database.sessions() as session:
            row = session.get(Setting, "update_interval_hours")
            return int(row.value) if row else 0

    def save_update_interval(self, hours: int) -> None:
        if not 0 <= hours <= 720:
            raise ValueError("更新间隔需为 0 到 720 小时。")
        with self.database.sessions.begin() as session:
            row = session.get(Setting, "update_interval_hours")
            if row is None:
                session.add(Setting(key="update_interval_hours", value=hours))
            else:
                row.value = hours

    def all(self) -> dict[str, object]:
        with self.database.sessions() as session:
            return {row.key: row.value for row in session.scalars(select(Setting))}
