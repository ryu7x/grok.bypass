from time     import time, sleep
from random   import uniform
from typing   import Optional, Callable, Any, Dict
from threading import Lock
from collections import deque


class RateLimiter:
    
    def __init__(
        self,
        base_delay: float = 0.5,
        max_delay: float = 15.0,
        jitter_factor: float = 0.2,
        min_request_interval: float = 0.5,
        burst_limit: int = 8,
        burst_window: float = 10.0,
    ) -> None:
        self._base_delay: float = base_delay
        self._max_delay: float = max_delay
        self._jitter_factor: float = jitter_factor
        self._min_interval: float = min_request_interval
        self._burst_limit: int = burst_limit
        self._burst_window: float = burst_window
        
        self._last_request: float = 0.0
        self._consecutive_failures: int = 0
        self._cooldown_until: float = 0.0
        self._request_times: deque = deque(maxlen=burst_limit)
        self._lock: Lock = Lock()
        
        self._hourly_requests: int = 0
        self._hourly_reset: float = time() + 3600
        self._daily_requests: int = 0
        self._daily_reset: float = time() + 86400

    def _calculate_backoff(self, multiplier: float = 1.0) -> float:
        delay = min(self._base_delay * (2 ** self._consecutive_failures) * multiplier, self._max_delay)
        jitter = uniform(-self._jitter_factor * delay, self._jitter_factor * delay)
        return max(0.5, delay + jitter)

    def _check_burst(self) -> float:
        now = time()
        while self._request_times and now - self._request_times[0] > self._burst_window:
            self._request_times.popleft()
        
        if len(self._request_times) >= self._burst_limit:
            oldest = self._request_times[0]
            wait_time = self._burst_window - (now - oldest)
            return max(0, wait_time)
        return 0

    def _update_counters(self) -> None:
        now = time()
        if now > self._hourly_reset:
            self._hourly_requests = 0
            self._hourly_reset = now + 3600
        if now > self._daily_reset:
            self._daily_requests = 0
            self._daily_reset = now + 86400

    def wait_if_needed(self) -> float:
        with self._lock:
            now = time()
            total_wait = 0.0
            
            if now < self._cooldown_until:
                wait_time = self._cooldown_until - now
                sleep(wait_time)
                total_wait += wait_time
                now = time()
            
            burst_wait = self._check_burst()
            if burst_wait > 0:
                sleep(burst_wait)
                total_wait += burst_wait
                now = time()
            
            time_since_last = now - self._last_request
            if time_since_last < self._min_interval:
                interval_wait = self._min_interval - time_since_last + uniform(0.1, 0.3)
                sleep(interval_wait)
                total_wait += interval_wait
            
            self._last_request = time()
            self._request_times.append(time())
            self._update_counters()
            self._hourly_requests += 1
            self._daily_requests += 1
            
            return total_wait

    def report_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def report_failure(self, is_rate_limit: bool = False, is_antibot: bool = False) -> float:
        with self._lock:
            self._consecutive_failures += 1
            
            if is_antibot:
                multiplier = 1.5
            elif is_rate_limit:
                multiplier = 1.2
            else:
                multiplier = 1.0
            
            backoff = self._calculate_backoff(multiplier)
            
            if is_rate_limit or is_antibot:
                self._cooldown_until = time() + backoff
                self._min_interval = min(self._min_interval * 1.1, 3.0)
            
            return backoff

    def report_antibot(self) -> float:
        return self.report_failure(is_rate_limit=False, is_antibot=True)

    def reset(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._cooldown_until = 0.0
            self._min_interval = 1.0
            self._request_times.clear()

    def is_in_cooldown(self) -> bool:
        return time() < self._cooldown_until

    def get_remaining_cooldown(self) -> float:
        remaining = self._cooldown_until - time()
        return max(0.0, remaining)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "consecutive_failures": self._consecutive_failures,
                "in_cooldown": self.is_in_cooldown(),
                "cooldown_remaining": self.get_remaining_cooldown(),
                "min_interval": self._min_interval,
                "hourly_requests": self._hourly_requests,
                "daily_requests": self._daily_requests,
                "burst_count": len(self._request_times),
            }

    def adaptive_delay(self) -> None:
        with self._lock:
            if self._consecutive_failures > 3:
                self._min_interval = min(self._min_interval * 1.3, 5.0)
            elif self._consecutive_failures == 0:
                self._min_interval = max(self._min_interval * 0.8, 0.3)


class RetryHandler:
    
    def __init__(
        self,
        max_retries: int = 5,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self._max_retries: int = max_retries
        self._limiter: RateLimiter = rate_limiter or RateLimiter()

    def execute_with_retry(
        self,
        func: Callable[..., Any],
        *args,
        on_rate_limit: Optional[Callable[[], None]] = None,
        on_antibot: Optional[Callable[[], None]] = None,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
        **kwargs,
    ) -> Any:
        last_error = None
        
        for attempt in range(self._max_retries + 1):
            try:
                self._limiter.wait_if_needed()
                result = func(*args, **kwargs)
                self._limiter.report_success()
                return result
            except RateLimitError as e:
                last_error = e
                backoff = self._limiter.report_failure(is_rate_limit=True)
                if on_rate_limit:
                    on_rate_limit()
                if on_retry:
                    on_retry(attempt, e)
                if attempt < self._max_retries:
                    sleep(backoff)
            except AntiBotError as e:
                last_error = e
                backoff = self._limiter.report_antibot()
                if on_antibot:
                    on_antibot()
                if on_retry:
                    on_retry(attempt, e)
                if attempt < self._max_retries:
                    sleep(backoff)
            except HeavyUsageError as e:
                last_error = e
                backoff = self._limiter.report_failure(is_rate_limit=True)
                if on_retry:
                    on_retry(attempt, e)
                if attempt < self._max_retries:
                    sleep(backoff)
            except Exception as e:
                last_error = e
                backoff = self._limiter.report_failure(is_rate_limit=False)
                if on_retry:
                    on_retry(attempt, e)
                if attempt < self._max_retries:
                    sleep(backoff)
        
        raise last_error if last_error else RuntimeError("Max retries exceeded")

    def get_limiter(self) -> RateLimiter:
        return self._limiter


class RateLimitError(Exception):
    pass


class AntiBotError(Exception):
    pass


class HeavyUsageError(Exception):
    pass


class CircuitBreaker:
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_requests: int = 3,
    ) -> None:
        self._failure_threshold: int = failure_threshold
        self._recovery_timeout: float = recovery_timeout
        self._half_open_requests: int = half_open_requests
        
        self._failures: int = 0
        self._last_failure: float = 0.0
        self._state: str = "closed"
        self._half_open_successes: int = 0
        self._lock: Lock = Lock()

    def can_proceed(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            
            if self._state == "open":
                if time() - self._last_failure > self._recovery_timeout:
                    self._state = "half-open"
                    self._half_open_successes = 0
                    return True
                return False
            
            if self._state == "half-open":
                return True
            
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == "half-open":
                self._half_open_successes += 1
                if self._half_open_successes >= self._half_open_requests:
                    self._state = "closed"
                    self._failures = 0
            else:
                self._failures = max(0, self._failures - 1)

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure = time()
            
            if self._state == "half-open":
                self._state = "open"
            elif self._failures >= self._failure_threshold:
                self._state = "open"

    def get_state(self) -> str:
        return self._state

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._half_open_successes = 0
