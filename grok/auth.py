from typing     import Optional, Dict, List
from pathlib    import Path
from secrets    import token_hex
from time       import time
from threading  import Lock
from .logger    import Log
import json


class APIKeyManager:
    
    KEYS_FILE: Path = Path("keys.json")
    KEY_PREFIX: str = "grok-sk-"
    
    def __init__(self) -> None:
        self._keys: Dict[str, dict] = {}
        self._lock: Lock = Lock()
        self._load_keys()
    
    def _load_keys(self) -> None:
        if self.KEYS_FILE.exists():
            try:
                with open(self.KEYS_FILE, "r") as f:
                    data = json.load(f)
                    self._keys = data.get("keys", {})
            except Exception:
                self._keys = {}
    
    def _save_keys(self) -> None:
        with open(self.KEYS_FILE, "w") as f:
            json.dump({"keys": self._keys}, f, indent=2)
    
    def generate_key(self, name: str = "default") -> str:
        with self._lock:
            key = f"{self.KEY_PREFIX}{token_hex(32)}"
            self._keys[key] = {
                "name": name,
                "created_at": int(time()),
                "requests": 0,
                "last_used": None,
                "active": True
            }
            self._save_keys()
            with Log.Context("Auth"):
                Log.Success(f"Generated API key: {name}")
            return key
    
    def validate_key(self, key: str) -> bool:
        if not key:
            return False
        
        key = key.replace("Bearer ", "").strip()
        
        with self._lock:
            if key not in self._keys:
                return False
            
            key_data = self._keys[key]
            if not key_data.get("active", True):
                return False
            
            key_data["requests"] += 1
            key_data["last_used"] = int(time())
            self._save_keys()
            return True
    
    def revoke_key(self, key: str) -> bool:
        with self._lock:
            if key in self._keys:
                self._keys[key]["active"] = False
                self._save_keys()
                return True
            return False

    def delete_key(self, key: str) -> bool:
        with self._lock:
            if key in self._keys:
                del self._keys[key]
                self._save_keys()
                return True
            return False
    
    def list_keys(self) -> List[dict]:
        with self._lock:
            result = []
            for key, data in self._keys.items():
                result.append({
                    "key": f"{key[:15]}...{key[-4:]}",
                    "name": data.get("name", "unnamed"),
                    "created_at": data.get("created_at"),
                    "requests": data.get("requests", 0),
                    "active": data.get("active", True)
                })
            return result
    
    def get_key_count(self) -> int:
        return len([k for k, v in self._keys.items() if v.get("active", True)])


_key_manager: Optional[APIKeyManager] = None


def get_key_manager() -> APIKeyManager:
    global _key_manager
    if _key_manager is None:
        _key_manager = APIKeyManager()
    return _key_manager
