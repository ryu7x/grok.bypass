from typing      import Optional, Dict, List, Any
from threading   import Lock
from concurrent.futures import ThreadPoolExecutor, Future
from time        import time, sleep
from grok        import Log
from grok.pool   import SessionPool, PooledSession, Fingerprints
from grok.limiter import RateLimiter


class GrokManager:
    
    def __init__(
        self,
        pool_size: int = 4,
        max_workers: int = 3,
        base_delay: float = 2.0,
        max_retries: int = 5,
    ) -> None:
        self._pool: SessionPool = SessionPool(pool_size=pool_size)
        self._limiter: RateLimiter = RateLimiter(base_delay=base_delay, max_delay=15.0)
        self._max_retries: int = max_retries
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock: Lock = Lock()
        self._request_id: int = 0
        self._stats: Dict[str, int] = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limit_retries": 0,
            "antibot_retries": 0,
            "images_generated": 0,
        }
        self._active_conversations: Dict[str, dict] = {}
        self._pool.warmup()

    def _get_request_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _handle_rate_limit(self, session: PooledSession, request_id: int, attempt: int) -> PooledSession:
        with self._lock:
            self._stats["rate_limit_retries"] += 1
        self._pool.report_rate_limit(session)
        backoff = self._limiter.report_failure(is_rate_limit=True)
        with Log.Context("RateLimit"):
            Log.Warning(f"Request {request_id} rate limited (attempt {attempt}), waiting {backoff:.1f}s")
        sleep(backoff)
        new_session = self._pool.get_fresh_session()
        with Log.Context("Session"):
            Log.Info(f"Switched to session #{new_session.session_id} ({new_session.fingerprint.impersonate})")
        return new_session

    def _handle_antibot(self, session: PooledSession, request_id: int, attempt: int) -> PooledSession:
        with self._lock:
            self._stats["antibot_retries"] += 1
        self._pool.retire_session(session)
        backoff = self._limiter.report_failure(is_rate_limit=False) * 1.5
        with Log.Context("AntiBot"):
            Log.Warning(f"Request {request_id} anti-bot triggered (attempt {attempt}), waiting {backoff:.1f}s")
        sleep(backoff)
        new_session = self._pool.get_fresh_session()
        with Log.Context("Session"):
            Log.Info(f"New session #{new_session.session_id} ({new_session.fingerprint.impersonate})")
        return new_session

    def ask(
        self,
        message: str,
        model: str = "grok-3-auto",
        extra_data: dict = None,
        conversation_id: str = None,
    ) -> dict:
        from grok.api import Grok
        
        request_id = self._get_request_id()
        
        with self._lock:
            self._stats["total_requests"] += 1

        with Log.Context("Request"):
            Log.Info(f"[{request_id}] {message[:60]}{'...' if len(message) > 60 else ''}")

        pooled_session = None
        
        for attempt in range(1, self._max_retries + 2):
            if pooled_session is None:
                pooled_session = self._pool.get_session()
            
            with Log.Context("Session"):
                Log.Debug(f"[{request_id}] #{pooled_session.session_id} {pooled_session.fingerprint.impersonate} (attempt {attempt})")
            
            try:
                grok = Grok(model=model)
                grok.session = pooled_session.session
                grok.keys = pooled_session.keys
                
                self._update_headers_from_fingerprint(grok, pooled_session)
                
                if conversation_id and conversation_id in self._active_conversations:
                    extra_data = self._active_conversations[conversation_id]
                
                result = grok.start_convo(message, extra_data=extra_data)
                
                if "error" in result:
                    error_type = result.get("error", "unknown")
                    should_retry = result.get("retry", False)
                    
                    if error_type == "ratelimit" and should_retry and attempt <= self._max_retries:
                        pooled_session = self._handle_rate_limit(pooled_session, request_id, attempt)
                        continue
                    
                    if error_type == "antibot" and should_retry and attempt <= self._max_retries:
                        pooled_session = self._handle_antibot(pooled_session, request_id, attempt)
                        continue
                    
                    if error_type == "heavy_usage" and should_retry and attempt <= self._max_retries:
                        pooled_session = self._handle_rate_limit(pooled_session, request_id, attempt)
                        continue
                    
                    with self._lock:
                        self._stats["failed_requests"] += 1
                    with Log.Context("Request"):
                        Log.Error(f"[{request_id}] Failed: {result.get('message', 'Unknown error')}")
                    return result
                
                self._pool.report_success(pooled_session)
                
                with self._lock:
                    self._stats["successful_requests"] += 1
                    if result.get("images"):
                        img_count = len(result["images"])
                        self._stats["images_generated"] += img_count
                        with Log.Context("Images"):
                            Log.Success(f"[{request_id}] Generated {img_count} image{'s' if img_count > 1 else ''}")
                
                self._limiter.report_success()
                
                if "extra_data" in result and result["extra_data"].get("conversationId"):
                    conv_id = result["extra_data"]["conversationId"]
                    self._active_conversations[conv_id] = result["extra_data"]
                
                with Log.Context("Request"):
                    Log.Success(f"[{request_id}] Complete")
                
                return result
                
            except Exception as e:
                error_msg = str(e)
                self._pool.retire_session(pooled_session)
                pooled_session = None
                
                with Log.Context("Request"):
                    Log.Error(f"[{request_id}] Exception: {error_msg}")
                
                if attempt <= self._max_retries:
                    backoff = self._limiter.report_failure()
                    with Log.Context("Retry"):
                        Log.Info(f"Waiting {backoff:.1f}s before retry {attempt + 1}")
                    sleep(backoff)
                else:
                    with self._lock:
                        self._stats["failed_requests"] += 1
                    return {"error": "exception", "message": error_msg, "retry": False}
        
        with self._lock:
            self._stats["failed_requests"] += 1
        return {"error": "max_retries", "message": "Max retries exceeded", "retry": False}

    def _update_headers_from_fingerprint(self, grok, pooled_session: PooledSession) -> None:
        fp = pooled_session.fingerprint
        grok.headers.LOAD.update(fp.to_headers())
        grok.headers.C_REQUEST.update(fp.to_headers())
        grok.headers.CONVERSATION.update(fp.to_headers())

    def ask_async(
        self,
        message: str,
        model: str = "grok-3-auto",
        extra_data: dict = None,
    ) -> Future:
        return self._executor.submit(self.ask, message, model, extra_data)

    def batch_ask(
        self,
        messages: List[str],
        model: str = "grok-3-auto",
    ) -> List[dict]:
        futures = [self.ask_async(msg, model) for msg in messages]
        return [f.result() for f in futures]

    def generate_images(self, prompt: str, model: str = "grok-3-auto") -> dict:
        with Log.Context("ImageGen"):
            Log.Info(f"Generating images for: {prompt[:50]}...")
        
        full_prompt = f"Generate an image: {prompt}"
        result = self.ask(full_prompt, model=model)
        
        if result.get("images"):
            with Log.Context("ImageGen"):
                Log.Success(f"Generated {len(result['images'])} images successfully")
        
        return result

    def analyze_image(self, image_path: str, prompt: str = "Describe this image in detail", model: str = "grok-3-auto") -> dict:
        from grok.api import Grok
        from pathlib import Path
        import base64
        
        path = Path(image_path)
        if not path.exists():
            with Log.Context("Analyze"):
                Log.Error(f"File not found: {image_path}")
            return {"error": "file_not_found", "message": f"File not found: {image_path}"}
        
        with Log.Context("Analyze"):
            Log.Info(f"Analyzing: {path.name}")
        
        request_id = self._get_request_id()
        
        with self._lock:
            self._stats["total_requests"] += 1
        
        pooled_session = self._pool.get_session()
        
        try:
            grok = Grok(model=model)
            grok.session = pooled_session.session
            grok.keys = pooled_session.keys
            self._update_headers_from_fingerprint(grok, pooled_session)
            
            upload_result = grok.upload_image(image_path)
            
            if "error" in upload_result:
                with Log.Context("Analyze"):
                    Log.Error(f"Upload failed: {upload_result.get('message')}")
                return upload_result
            
            with Log.Context("Analyze"):
                Log.Success("Image uploaded, analyzing...")
            
            image_attachment = {
                "url": upload_result.get("image_url") or upload_result.get("data", {}).get("url"),
                "mimeType": "image/jpeg"
            }
            
            result = grok.start_convo(prompt, extra_data=None)
            
            if result.get("response"):
                with self._lock:
                    self._stats["successful_requests"] += 1
                with Log.Context("Analyze"):
                    Log.Success(f"[{request_id}] Analysis complete")
            
            return result
            
        except Exception as e:
            self._pool.retire_session(pooled_session)
            with Log.Context("Analyze"):
                Log.Error(f"Exception: {str(e)}")
            return {"error": "exception", "message": str(e)}

    def get_stats(self) -> dict:
        with self._lock:
            pool_stats = self._pool.get_stats()
            return {
                **self._stats,
                **pool_stats,
                "success_rate": (
                    self._stats["successful_requests"] / self._stats["total_requests"] * 100
                    if self._stats["total_requests"] > 0 else 0
                ),
            }

    def print_stats(self) -> None:
        stats = self.get_stats()
        Log.Divider()
        table = Log.Table(["Metric", "Value"], style="rounded")
        table.add_row(["Requests", str(stats["total_requests"])])
        table.add_row(["Success", str(stats["successful_requests"])])
        table.add_row(["Failed", str(stats["failed_requests"])])
        table.add_row(["Success Rate", f"{stats['success_rate']:.1f}%"])
        table.add_row(["Rate Limits", str(stats["rate_limit_retries"])])
        table.add_row(["Anti-bot Hits", str(stats["antibot_retries"])])
        table.add_row(["Images", str(stats["images_generated"])])
        table.add_row(["Active Sessions", str(stats["active_sessions"])])
        table.add_row(["Cooling Down", str(stats.get("cooling_sessions", 0))])
        table.print()
        Log.Divider()

    def refresh_pool(self) -> None:
        with Log.Context("Pool"):
            Log.Info("Refreshing all sessions...")
        self._pool.refresh_all()
        self._limiter.reset()
        with Log.Context("Pool"):
            stats = self._pool.get_stats()
            Log.Success(f"Pool refreshed: {stats['active_sessions']} sessions ready")

    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        return self._active_conversations.get(conversation_id)

    def continue_conversation(self, conversation_id: str, message: str) -> dict:
        extra_data = self.get_conversation(conversation_id)
        if not extra_data:
            return {"error": "not_found", "message": f"Conversation {conversation_id} not found", "retry": False}
        return self.ask(message, extra_data=extra_data, conversation_id=conversation_id)

    def shutdown(self) -> None:
        with Log.Context("Manager"):
            Log.Info("Shutting down...")
        self._executor.shutdown(wait=True)
        with Log.Context("Manager"):
            Log.Success("Shutdown complete")
