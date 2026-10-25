from __future__ import annotations

import dataclasses
import enum
import uuid
from typing import Any

from .errors import ValidationError


class TaskState(enum.StrEnum):
    DRAFT = "draft"
    SCANNING = "scanning"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


IN_FLIGHT = {TaskState.SCANNING, TaskState.RUNNING, TaskState.STOPPING, TaskState.VERIFYING}


@dataclasses.dataclass(frozen=True, slots=True)
class TransferSettings:
    threads: int = 8
    retries: int = 2
    wait_seconds: int = 2
    hash_verify: bool = False
    fat_times: bool = False
    rate_mib: int = 0

    def __post_init__(self) -> None:
        for name, minimum, maximum in [("threads", 1, 128), ("retries", 0, 10),
                                        ("wait_seconds", 0, 60), ("rate_mib", 0, 10000)]:
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValidationError(f"{name} must be between {minimum} and {maximum}.")
        if type(self.hash_verify) is not bool or type(self.fat_times) is not bool:
            raise ValidationError("Verification settings must be boolean.")


@dataclasses.dataclass(frozen=True, slots=True)
class TaskSpec:
    sources: tuple[str, ...]
    destination: str
    folder_itself: bool = True
    settings: TransferSettings = dataclasses.field(default_factory=TransferSettings)
    id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        uuid.UUID(data["id"])
        return cls(tuple(data["sources"]), data["destination"], data["folder_itself"],
                   TransferSettings(**data["settings"]), data["id"])


@dataclasses.dataclass(frozen=True, slots=True)
class CopyRoot:
    source: str
    destination: str
    file_name: str | None = None


@dataclasses.dataclass(slots=True)
class Progress:
    task_id: str
    state: str
    total_bytes: int = 0
    total_files: int = 0
    matched_bytes: int = 0
    matched_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    verified_files: int = 0
    current_file: str = ""
    elapsed: float = 0.0
    rate: float = 0.0
    average_rate: float = 0.0
    eta: float | None = None
    message: str = ""
    final: bool = False

    @property
    def percent(self) -> float:
        if self.total_bytes:
            return min(100.0, self.matched_bytes * 100 / self.total_bytes)
        if self.total_files:
            return min(100.0, self.matched_files * 100 / self.total_files)
        return 100.0 if self.final and self.state == TaskState.COMPLETED else 0.0
