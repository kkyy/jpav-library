"""Opt-in periodic refresh of bound official metadata sources."""

import logging
from datetime import UTC, datetime, timedelta
from threading import Event

from sqlalchemy import select

from av_library.db.database import Database
from av_library.db.models import Actress, ActressSource, SyncHistory
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.providers.ideapocket_public import IdeaPocketPublicProvider
from av_library.providers.s1_public import S1PublicProvider
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
        providers = {
            "s1_public": S1PublicProvider(),
            "ideapocket_public": IdeaPocketPublicProvider(),
        }
        try:
            providers["dmm"] = DmmProvider()
        except ProviderError:
            logger.info("DMM periodic sync skipped: credentials unavailable")
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.database.sessions() as session:
            due = []
            for actress_id, provider_id in session.execute(
                select(Actress.id, ActressSource.provider_id)
                .join(ActressSource, ActressSource.actress_id == Actress.id)
                .where(ActressSource.provider_id.in_(providers))
            ):
                latest = session.scalar(
                    select(SyncHistory.started_at)
                    .where(
                        SyncHistory.actress_id == actress_id,
                        SyncHistory.provider_id == provider_id,
                    )
                    .order_by(SyncHistory.id.desc())
                    .limit(1)
                )
                if latest is None or latest + timedelta(hours=hours) <= now:
                    due.append((actress_id, provider_id))
        done = failed = 0
        for actress_id, provider_id in due:
            if cancel.is_set():
                break
            try:
                history = MetadataService(self.database).sync(
                    actress_id, providers[provider_id], cancel
                )
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
