import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path

    @classmethod
    def resolve(cls, override: str | None = None) -> "AppPaths":
        if override:
            root = Path(override).expanduser().resolve()
        else:
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
            root = base / "JPAVLibrary"
        return cls(root)

    @property
    def database(self) -> Path:
        return self.data_dir / "library.sqlite3"

    @property
    def covers(self) -> Path:
        return self.data_dir / "covers"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs(self) -> Path:
        return self.data_dir / "logs"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers.mkdir(exist_ok=True)
        self.backups.mkdir(exist_ok=True)
        self.logs.mkdir(exist_ok=True)
