from dataclasses import dataclass, field
from typing      import Optional, List, Dict
from curl_cffi   import requests
from random      import choice, shuffle, uniform
from time        import time, sleep
from threading   import Lock
from json        import dump, load
from os          import path
from grok        import Anon, Headers, Log


CF_COOKIE_KEYS = {'cf_clearance', '__cf_bm', '__cflb', '_cfuvid', 'cf_chl_2', 'cf_chl_prog'}


@dataclass
class Fingerprint:
    impersonate: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    sec_ch_ua_mobile: str = "?0"
    accept_language: str = "en-US,en;q=0.9"

    def to_headers(self) -> dict:
        return {
            "user-agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "accept-language": self.accept_language,
        }


class Fingerprints:
    
    PROFILES: List[Fingerprint] = [
        Fingerprint(
            impersonate="chrome136",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            sec_ch_ua='"Google Chrome";v="136", "Chromium";v="136", "Not A(Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language="en-US,en;q=0.9",
        ),
        Fingerprint(
            impersonate="chrome131",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not A(Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language="en-GB,en;q=0.9",
        ),
        Fingerprint(
            impersonate="chrome136",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            sec_ch_ua='"Google Chrome";v="136", "Chromium";v="136", "Not A(Brand";v="24"',
            sec_ch_ua_platform='"macOS"',
            accept_language="en-US,en;q=0.9,es;q=0.8",
        ),
        Fingerprint(
            impersonate="chrome131",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not A(Brand";v="24"',
            sec_ch_ua_platform='"macOS"',
            accept_language="en-CA,en;q=0.9",
        ),
    ]

    @classmethod
    def random(cls) -> Fingerprint:
        return choice(cls.PROFILES)
    
    @classmethod
    def get_all(cls) -> List[Fingerprint]:
        profiles = cls.PROFILES.copy()
        shuffle(profiles)
        return profiles


@dataclass
class PooledSession:
    session: requests.Session
    fingerprint: Fingerprint
    keys: dict
    session_id: int = 0
    created_at: float = field(default_factory=time)
    last_used: float = field(default_factory=time)
    request_count: int = 0
    rate_limited_at: Optional[float] = None
    is_stale: bool = False
    cooldown_until: float = 0.0

    def mark_used(self) -> None:
        self.last_used = time()
        self.request_count += 1

    def mark_rate_limited(self, cooldown: float = 10.0) -> None:
        self.rate_limited_at = time()
        self.cooldown_until = time() + cooldown
        self.is_stale = True

    def is_healthy(self) -> bool:
        if self.is_stale:
            return False
        if self.rate_limited_at and time() < self.cooldown_until:
            return False
        if self.request_count > 150:
            return False
        if time() - self.created_at > 3600:
            return False
        return True

    def get_age(self) -> float:
        return time() - self.created_at
    
    def can_use(self) -> bool:
        if time() < self.cooldown_until:
            return False
        return self.is_healthy()


class SessionPool:
    
    COOKIE_FILE = 'grok/mappings/cookies.json'
    
    def __init__(self, pool_size: int = 5) -> None:
        self._pool: List[PooledSession] = []
        self._pool_size: int = pool_size
        self._lock: Lock = Lock()
        self._current_index: int = 0
        self._session_counter: int = 0
        self._min_request_interval: float = 0.6
        self._last_request_time: float = 0.0
        self._cf_cookies: Dict[str, str] = {}
        self._stats: Dict[str, int] = {
            "sessions_created": 0,
            "sessions_retired": 0,
            "rate_limits_hit": 0,
            "requests_made": 0,
            "cookie_reuses": 0,
            "cookie_refreshes": 0,
        }
        self._load_cookies()
        self._initialize_pool()

    def _initialize_pool(self) -> None:
        fingerprints = Fingerprints.get_all()
        for i in range(min(self._pool_size, len(fingerprints))):
            self._pool.append(self._create_session(fingerprints[i]))

    def _create_session(self, fingerprint: Optional[Fingerprint] = None) -> PooledSession:
        if not fingerprint:
            fingerprint = Fingerprints.random()
        
        session = requests.Session(
            impersonate=fingerprint.impersonate,
            default_headers=False
        )
        
        if self._cf_cookies:
            for k, v in self._cf_cookies.items():
                session.cookies.set(k, v, domain=".grok.com")
            self._stats["cookie_reuses"] += 1
        
        self._session_counter += 1
        self._stats["sessions_created"] += 1
        
        return PooledSession(
            session=session,
            fingerprint=fingerprint,
            keys=Anon.generate_keys(),
            session_id=self._session_counter,
        )

    def _load_cookies(self) -> None:
        """Load cookies from disk — always load, never check age."""
        try:
            if path.exists(self.COOKIE_FILE):
                with open(self.COOKIE_FILE, 'r') as f:
                    data = load(f)
                self._cf_cookies = {k: v for k, v in data.items() if k != "_saved_at"}
                if self._cf_cookies:
                    Log.Info(f"Loaded {len(self._cf_cookies)} cached CF cookies")
        except Exception:
            pass

    def _save_cookies(self) -> None:
        try:
            data = {**self._cf_cookies, "_saved_at": time()}
            with open(self.COOKIE_FILE, 'w') as f:
                dump(data, f)
        except Exception:
            pass

    def extract_cf_cookies(self, session: requests.Session) -> None:
        """Extract ALL cookies from any session response — success or failure."""
        try:
            cookies = session.cookies.get_dict()
            if cookies:
                old = self._cf_cookies.copy()
                self._cf_cookies.update(cookies)
                if cookies != old:
                    self._stats["cookie_refreshes"] += 1
                self._save_cookies()
        except Exception:
            pass

    def warmup(self) -> None:
        """Pre-fetch cookies with an initial GET to grok.com."""
        try:
            fp = Fingerprints.random()
            session = requests.Session(impersonate=fp.impersonate, default_headers=False)
            
            if self._cf_cookies:
                for k, v in self._cf_cookies.items():
                    session.cookies.set(k, v, domain=".grok.com")
            
            headers = Headers().LOAD
            headers.update(fp.to_headers())
            session.headers = headers
            
            # First hit to main page to get initial cookies
            resp = session.get('https://grok.com/', timeout=15)
            
            # Extract whatever we got
            self.extract_cf_cookies(session)
            
            if self._cf_cookies:
               Log.Success(f"Warmup complete, saved {len(self._cf_cookies)} cookies to disk")
            else:
               Log.Warning("Warmup done, no cookies received")
                
        except Exception as e:
            Log.Warning(f"Warmup failed: {e}")

    def _throttle(self) -> None:
        now = time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            jitter = uniform(0.05, 0.2)
            sleep_time = self._min_request_interval - elapsed + jitter
            sleep(sleep_time)
        self._last_request_time = time()

    def get_session(self) -> PooledSession:
        with self._lock:
            self._throttle()
            self._cleanup_stale()
            
            while len(self._pool) < self._pool_size:
                self._pool.append(self._create_session())
            
            usable = [s for s in self._pool if s.can_use()]
            if not usable:
                new_session = self._create_session()
                self._pool.append(new_session)
                usable = [new_session]
            
            usable.sort(key=lambda s: s.request_count)
            selected = usable[0]
            selected.mark_used()
            self._stats["requests_made"] += 1
            
            return selected

    def report_rate_limit(self, pooled_session: PooledSession) -> None:
        with self._lock:
            self.extract_cf_cookies(pooled_session.session)
            cooldown = 10.0 + uniform(2, 5)
            pooled_session.mark_rate_limited(cooldown)
            self._stats["rate_limits_hit"] += 1

    def report_success(self, pooled_session: PooledSession) -> None:
        with self._lock:
            self.extract_cf_cookies(pooled_session.session)

    def retire_session(self, pooled_session: PooledSession) -> None:
        with self._lock:
            self.extract_cf_cookies(pooled_session.session)
            pooled_session.is_stale = True
            self._stats["sessions_retired"] += 1

    def _cleanup_stale(self) -> None:
        now = time()
        for s in self._pool:
            if s.rate_limited_at and now >= s.cooldown_until:
                s.rate_limited_at = None
                s.is_stale = False
                s.request_count = 0
        
        self._pool = [s for s in self._pool if not s.is_stale or (s.cooldown_until > 0 and now < s.cooldown_until)]

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            usable = len([s for s in self._pool if s.can_use()])
            cooling = len([s for s in self._pool if s.rate_limited_at and time() < s.cooldown_until])
            return {
                **self._stats,
                "active_sessions": usable,
                "cooling_sessions": cooling,
                "total_sessions": len(self._pool),
                "cf_cookies": len(self._cf_cookies),
            }

    def refresh_all(self) -> None:
        with self._lock:
            for session in self._pool:
                self.extract_cf_cookies(session.session)
                session.is_stale = True
            self._pool.clear()
            self._initialize_pool()

    def get_fresh_session(self) -> PooledSession:
        with self._lock:
            new_session = self._create_session()
            self._pool.append(new_session)
            new_session.mark_used()
            self._stats["requests_made"] += 1
            return new_session
