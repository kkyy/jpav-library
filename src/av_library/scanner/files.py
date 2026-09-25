import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from av_library.parsing.codes import CodeParser, ParseResult

DEFAULT_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm")


def path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


@dataclass(frozen=True)
class ScanOptions:
    recursive: bool = True
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS


@dataclass(frozen=True)
class ScannedFile:
    path: str
    filename: str
    parsed: ParseResult


@dataclass
class ScanSnapshot:
    files: list[ScannedFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error_count: int = 0
    skipped_links: int = 0
    cancelled: bool = False

    def add_error(self, path: str, error: OSError):
        self.error_count += 1
        if len(self.errors) < 20:
            self.errors.append(f"{path}: {error}")


def scan_filenames(
    root: str,
    options: ScanOptions,
    cancel: Event,
    progress: Callable[[int], None] = lambda _count: None,
) -> ScanSnapshot:
    result = ScanSnapshot()
    parser = CodeParser()
    pending = [root]
    while pending:
        if cancel.is_set():
            result.cancelled = True
            break
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cancel.is_set():
                        result.cancelled = True
                        break
                    try:
                        if entry.is_symlink():
                            result.skipped_links += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            # Junctions and other directory reparse points can escape the root.
                            if (
                                os.name == "nt"
                                and getattr(
                                    entry.stat(follow_symlinks=False), "st_file_attributes", 0
                                )
                                & stat.FILE_ATTRIBUTE_REPARSE_POINT
                            ):
                                result.skipped_links += 1
                                continue
                            if options.recursive:
                                pending.append(entry.path)
                        elif Path(
                            entry.name
                        ).suffix.lower() in options.extensions and entry.is_file(
                            follow_symlinks=False
                        ):
                            result.files.append(
                                ScannedFile(
                                    os.path.abspath(entry.path),
                                    entry.name,
                                    parser.parse_filename(entry.name),
                                )
                            )
                            if len(result.files) % 100 == 0:
                                progress(len(result.files))
                    except OSError as error:
                        result.add_error(entry.path, error)
        except OSError as error:
            result.add_error(directory, error)
    result.cancelled = result.cancelled or cancel.is_set()
    progress(len(result.files))
    return result
