"""Process-owned reservations for optional provider capture memory."""

from __future__ import annotations

from dataclasses import dataclass

MAX_ITEMS = 64
RETAINED_BYTES = 8 * 1024 * 1024
WORKSPACE_BYTES = 8 * 1024 * 1024
ITEM_BYTES = 4 * 1024 * 1024
METADATA_BYTES = 64 * 1024
ENCODING_WORKSPACE_BYTES = 7 * 1024 * 1024
# Size/shape inspection uses bounded iterators and 1024-character JSON pieces.
# Its reservation ends before the actual encoder's reservation starts.
COUNTING_WORKSPACE_BYTES = 512 * 1024
SMALL_ENCODING_BASE_BYTES = 1536 * 1024


class CaptureBudget:
    def __init__(self) -> None:
        self.items = 0
        self.retained_bytes = 0
        self.workspace_bytes = 0
        self.high_water_bytes = 0
        self.high_water_items = 0
        self.accepting = True

    @property
    def available_retained_bytes(self) -> int:
        """Additional bytes available without consuming an existing reservation."""
        return RETAINED_BYTES - self.retained_bytes

    def reserve(self, size: int = ITEM_BYTES) -> Reservation | None:
        if not self.accepting or self.items >= MAX_ITEMS:
            return None
        if self.retained_bytes + size > RETAINED_BYTES:
            return None
        self.items += 1
        self.retained_bytes += size
        self.watermark()
        return Reservation(self, size)

    def workspace(self, size: int) -> bool:
        if size < 0 or self.workspace_bytes + size > WORKSPACE_BYTES:
            return False
        self.workspace_bytes += size
        self.watermark()
        return True

    def release_workspace(self, size: int) -> None:
        self.workspace_bytes -= size
        assert self.workspace_bytes >= 0

    def watermark(self) -> None:
        self.high_water_bytes = max(
            self.high_water_bytes, self.retained_bytes + self.workspace_bytes
        )
        self.high_water_items = max(self.high_water_items, self.items)


@dataclass(slots=True)
class Reservation:
    budget: CaptureBudget
    size: int
    released: bool = False

    def resize(self, size: int) -> bool:
        if self.released or size < 0:
            return False
        difference = size - self.size
        if self.budget.retained_bytes + difference > RETAINED_BYTES:
            return False
        self.budget.retained_bytes += difference
        self.size = size
        self.budget.watermark()
        return True

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.budget.items -= 1
        self.budget.retained_bytes -= self.size
        assert self.budget.items >= 0 and self.budget.retained_bytes >= 0
