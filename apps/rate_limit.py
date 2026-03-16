"""Per-user rate limiting with sliding window."""

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _UserBucket:
    timestamps: list[float] = field(default_factory=list)


class RateLimiter:
    """Sliding-window rate limiter.

    Args:
        max_requests: maximum requests per window
        window_seconds: window size in seconds
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[int, _UserBucket] = defaultdict(_UserBucket)

    def check(self, user_id: int) -> tuple[bool, float]:
        """Check if user can proceed.

        Returns:
            (allowed, retry_after_seconds)
            If allowed, retry_after is 0.
        """
        now = time.monotonic()
        bucket = self._buckets[user_id]
        cutoff = now - self.window_seconds
        bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

        if len(bucket.timestamps) >= self.max_requests:
            oldest = bucket.timestamps[0]
            retry_after = oldest + self.window_seconds - now
            return False, max(retry_after, 0.1)

        bucket.timestamps.append(now)
        return True, 0.0

    def remaining(self, user_id: int) -> int:
        """How many requests user has left in current window."""
        now = time.monotonic()
        bucket = self._buckets[user_id]
        cutoff = now - self.window_seconds
        active = sum(1 for t in bucket.timestamps if t > cutoff)
        return max(0, self.max_requests - active)


# Global instance — 10 messages per 60 seconds per user
rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
