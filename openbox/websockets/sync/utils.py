from __future__ import annotations

import time
from typing import Optional


__all__ = ["Deadline"]


class Deadline:
    """Manage timeouts across multiple steps.

    Args:     timeout: Time available in seconds or :obj:`None` if there is no
    limit.
    """

    def __init__(self, timeout: Optional[float]) -> None:
        self.deadline: Optional[float]
        if timeout is None:
            self.deadline = None
        else:
            self.deadline = time.monotonic() + timeout

    def timeout(self, *, raise_if_elapsed: bool = True) -> Optional[float]:
        """Calculate a timeout from a deadline.

        Args:     raise_if_elapsed (bool): Whether to raise :exc:`TimeoutError`
        if the deadline lapsed.

        Raises:     TimeoutError: If the deadline lapsed.

        Returns:     Time left in seconds or :obj:`None` if there is no limit.
        """
        if self.deadline is None:
            return None
        timeout = self.deadline - time.monotonic()
        if raise_if_elapsed and timeout <= 0:
            raise TimeoutError("timed out")
        return timeout
