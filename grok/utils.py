from typing    import Callable, Any, Optional, Type, List, Dict
from functools import wraps
from pathlib   import Path
from time      import time
from .logger   import Log
from curl_cffi import requests as curl_requests
import os


class Run:
    
    @staticmethod
    def Error(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                Run.handle_error(e)
                return None 
        return wrapper

    @staticmethod
    def handle_error(exception: Exception) -> Optional[None]:
        Log.Error(f"Fatal: {exception}")
        exit()
        

class Utils:
    
    @staticmethod
    def between(
        main_text: Optional[str],
        value_1: Optional[str],
        value_2: Optional[str],
        ) -> Type[str]:
        return main_text.split(value_1)[1].split(value_2)[0]


class ImageDownloader:
    
    BASE_URL: str = "https://assets.grok.com/"
    SAVE_DIR: Path = Path("images")
    
    @classmethod
    def ensure_dir(cls) -> None:
        cls.SAVE_DIR.mkdir(exist_ok=True)
    
    @classmethod
    def download(cls, image_paths: List[str], cookies: Dict[str, str] = None) -> List[str]:
        if not image_paths:
            return []
        
        cls.ensure_dir()
        saved_files: List[str] = []
        
        session = curl_requests.Session(impersonate="chrome136")
        if cookies:
            session.cookies.update(cookies)
        
        headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "accept-encoding": "gzip, deflate, br",
            "referer": "https://grok.com/",
            "sec-fetch-dest": "image",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "same-site",
        }
        
        for i, path in enumerate(image_paths):
            try:
                url = f"{cls.BASE_URL}{path}" if not path.startswith("http") else path
                
                timestamp = int(time() * 1000)
                filename = f"grok_{timestamp}_{i + 1}.jpg"
                filepath = cls.SAVE_DIR / filename
                
                response = session.get(url, headers=headers, timeout=30)
                
                if response.status_code != 200:
                    with Log.Context("Download"):
                        Log.Warning(f"Status {response.status_code} for image {i + 1}")
                    continue
                
                with open(filepath, "wb") as f:
                    f.write(response.content)
                
                saved_files.append(str(filepath))
                
            except Exception as e:
                with Log.Context("Download"):
                    Log.Error(f"Failed to save image {i + 1}: {e}")
        
        return saved_files
    
    @classmethod
    def get_saved_count(cls) -> int:
        cls.ensure_dir()
        return len(list(cls.SAVE_DIR.glob("*.jpg")))