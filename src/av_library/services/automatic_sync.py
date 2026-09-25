"""Opt-in periodic refresh of already-confirmed DMM identities."""

import logging
from datetime import UTC, datetime, timedelta
from threading import Event

from sqlalchemy import select

from av_library.db.database import Database
from av_library.db.models import Actress, ActressSource, SyncHistory
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.services.metadata_sync import MetadataService
from av_library.services.settings import SettingsService

logger = logging.getLogger("av_library.automatic_sync")


class AutomaticSyncService:
    def __init__(self, database: Database):
        self.database = database

    def run_due(self, cancel: Event) -> tuple[int, int]:
        hours = SettingsService(self.database).update_interval_hours()
        if hours == 0:
            return 0, 0
        try:
            provider = DmmProvider()
        except ProviderError:
            logger.info("Automatic sync skipped: DMM credentials unavailable")
            return 0, 0
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.database.sessions() as session:
            due = []
            for actress in session.scalars(
                select(Actress).join(
                    ActressSource,
                    (ActressSource.actress_id == Actress.id)
                    & (ActressSource.provider_id == provider.id),
                )
            ):
                latest = session.scalar(
                    select(SyncHistory.started_at)
                    .where(
                        SyncHistory.actress_id == actress.id,
                        SyncHistory.provider_id == provider.id,
                    )
                    .order_by(SyncHistory.id.desc())
                    .limit(1)
                )
                if latest is None or latest + timedelta(hours=hours) <= now:
                    due.append(actress.id)
        done = failed = 0
        for actress_id in due:
            if cancel.is_set():
                break
            try:
                history = MetadataService(self.database).sync(actress_id, provider, cancel)
                if history.status == "success":
                    done += 1
            except Exception as error:  # noqa: BLE001 -- keep scheduled refresh running
                failed += 1
                logger.error(
                    "Automatic sync failed actress_id=%s error=%s",
                    actress_id,
                    type(error).__name__,
                )
        return done, failed
