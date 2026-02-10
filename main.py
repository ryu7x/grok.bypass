import threading
from fastapi      import FastAPI, HTTPException, Request, Header
from pydantic     import BaseModel
from typing       import Optional, List
from grok         import Log, GrokManager, ImageDownloader
from grok.auth    import get_key_manager
from uvicorn      import run


from hashlib import md5
from fastapi.responses import Response
from curl_cffi import requests as curl_requests
from typing import Dict, Any

CDN_BASE = "https://assets.grok.com/"

app = FastAPI(title="Grok API", version="2.0.0")
manager = GrokManager(pool_size=8, max_workers=4, max_retries=4, base_delay=0.8)

# Image proxy cache: {image_id: {"url": full_url, "cookies": {...}, "created": timestamp}}
_image_cache: Dict[str, Dict[str, Any]] = {}


def _cache_images(image_paths: list, cookies: dict = None, base_url: str = "") -> list:
    """Cache image URLs with session cookies and return proxy URLs."""
    from time import time as _time
    proxied = []
    for img_path in (image_paths or []):
        full_url = img_path if img_path.startswith("http") else f"{CDN_BASE}{img_path}"
        image_id = md5(full_url.encode()).hexdigest()[:16]
        _image_cache[image_id] = {
            "url": full_url,
            "cookies": cookies or {},
            "created": _time(),
        }
        proxy_url = f"{base_url}/v1/images/proxy/{image_id}"
        proxied.append(proxy_url)
    return proxied


def _get_base_url(request: Request) -> str:
    """Build the public base URL from the incoming request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}"


def verify_api_key(authorization: Optional[str] = Header(None)) -> bool:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": {"message": "Missing API key", "type": "invalid_request_error", "code": "missing_api_key"}})
    key_manager = get_key_manager()
    if not key_manager.validate_key(authorization):
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}})
    return True


@app.get("/v1/images/proxy/{image_id}")
async def proxy_image(image_id: str):
    # Public endpoint for image embedding
    """Proxy that fetches images from assets.grok.com using cached session cookies."""
    from time import time as _time
    entry = _image_cache.get(image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Image not found or expired")
    
    # Clean up old entries (older than 30 minutes)
    cutoff = _time() - 1800
    expired = [k for k, v in _image_cache.items() if v["created"] < cutoff]
    for k in expired:
        _image_cache.pop(k, None)
    
    try:
        session = curl_requests.Session(impersonate="chrome136")
        if entry["cookies"]:
            session.cookies.update(entry["cookies"])
        
        headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "accept-encoding": "gzip, deflate, br",
            "referer": "https://grok.com/",
            "sec-fetch-dest": "image",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "same-site",
        }
        
        resp = session.get(entry["url"], headers=headers, timeout=30)
        
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Upstream returned {resp.status_code}")
        
        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {str(e)}")


class ConversationRequest(BaseModel):
    message: str
    model: str = "grok-3-auto"
    extra_data: Optional[dict] = None
    conversation_id: Optional[str] = None


class ImageRequest(BaseModel):
    prompt: str
    model: str = "grok-3-auto"


@app.post("/ask")
async def create_conversation(req: Request, request: ConversationRequest, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    if not request.message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    try:
        if request.conversation_id:
            answer: dict = manager.continue_conversation(
                conversation_id=request.conversation_id,
                message=request.message
            )
        else:
            answer: dict = manager.ask(
                message=request.message,
                model=request.model,
                extra_data=request.extra_data
            )

        if "error" in answer and not answer.get("retry"):
            raise HTTPException(status_code=500, detail=answer.get("message", "Unknown error"))
        
        images = answer.get("images", [])
        cookies = answer.get("extra_data", {}).get("cookies", {})
        base_url = _get_base_url(req)
        proxy_urls = _cache_images(images, cookies=cookies, base_url=base_url) if images else []
        
        return {
            "status": "success",
            "response": answer.get("response"),
            "images": proxy_urls,
            "conversation_id": answer.get("extra_data", {}).get("conversationId")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_image(req: Request, request: ImageRequest, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    try:
        answer = manager.generate_images(prompt=request.prompt, model=request.model)
        if "error" in answer:
            raise HTTPException(status_code=500, detail=answer.get("message"))
        
        images = answer.get("images", [])
        cookies = answer.get("extra_data", {}).get("cookies", {})
        base_url = _get_base_url(req)
        proxy_urls = _cache_images(images, cookies=cookies, base_url=base_url) if images else []
        
        return {
            "status": "success",
            "images": proxy_urls,
            "response": answer.get("response")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    return manager.get_stats()


@app.post("/refresh")
async def refresh_pool(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    manager.refresh_pool()
    return {"status": "success"}


@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    conv = manager.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "success", "conversation_id": conversation_id}


def start_api():
    run(app, host="0.0.0.0", port=6969, log_level="error")


def terminal_chat():
    Log.Section("GROK", start_rgb=(88, 101, 242), end_rgb=(114, 137, 218))
    Log.Blank()
    
    with Log.Context("System"):
        Log.Info("Commands stats, refresh, new, images, generate, analyze, exit")
    
    conversation_id = None
    last_cookies = {}
    
    while True:
        try:
            Log.Blank()
            message = input("YOU: ").strip()
            
            if not message:
                continue
            
            if message.lower() in ["exit", "quit", "q"]:
                manager.print_stats()
                break
            
            if message.lower() == "stats":
                manager.print_stats()
                continue
            
            if message.lower() == "refresh":
                manager.refresh_pool()
                continue
            
            if message.lower() == "new":
                conversation_id = None
                last_cookies = {}
                with Log.Context("System"):
                    Log.Success("New conversation started")
                continue
            
            if message.lower() == "images":
                count = ImageDownloader.get_saved_count()
                stats = manager.get_stats()
                with Log.Context("Images"):
                    Log.Info(f"Generated: {stats['images_generated']} | Saved: {count}")
                continue
            
            if message.lower().startswith("analyze "):
                parts = message[8:].strip().split(" ", 1)
                image_path = parts[0]
                prompt = parts[1] if len(parts) > 1 else "Describe this image in detail"
                
                if not image_path:
                    with Log.Context("System"):
                        Log.Warning("Usage: analyze <path> [prompt]")
                    continue
                
                with Log.Spinner("Analyzing", style="dots2") as spinner:
                    data = manager.analyze_image(image_path, prompt)
                    spinner.stop("Done")
                
                if data.get("response"):
                    Log.Blank()
                    Log.Gradient("GROK:", (88, 101, 242), (114, 137, 218))
                    print(data["response"])
                elif "error" in data:
                    with Log.Context("Error"):
                        Log.Error(data.get("message", str(data)))
                continue
            
            if message.lower().startswith("generate "):
                prompt = message[9:].strip()
                if not prompt:
                    with Log.Context("System"):
                        Log.Warning("Please provide a prompt: generate <your prompt>")
                    continue
                
                with Log.Spinner("Generating", style="dots2") as spinner:
                    data = manager.generate_images(prompt)
                    spinner.stop("Done")
                
                if data.get("response"):
                    Log.Blank()
                    Log.Gradient("GROK:", (88, 101, 242), (114, 137, 218))
                    print(data["response"])
                
                if data.get("extra_data", {}).get("cookies"):
                    last_cookies = data["extra_data"]["cookies"]
                
                if data.get("images"):
                    Log.Blank()
                    with Log.Context("Images"):
                        Log.Info(f"Downloading {len(data['images'])} images...")
                    
                    saved = ImageDownloader.download(data["images"], cookies=last_cookies)
                    
                    if saved:
                        with Log.Context("Images"):
                            Log.Success(f"Saved {len(saved)} to ./images/")
                        for path in saved:
                            print(f"  → {path}")
                    else:
                        with Log.Context("Images"):
                            Log.Warning("Could not save images")
                elif "error" in data:
                    with Log.Context("Error"):
                        Log.Error(data.get("message", str(data)))
                continue
            
            with Log.Spinner("Processing", style="dots2") as spinner:
                if conversation_id:
                    data = manager.continue_conversation(conversation_id, message)
                else:
                    data = manager.ask(message)
                spinner.stop("Done")
            
            if "response" in data and data["response"]:
                Log.Blank()
                Log.Gradient("GROK:", (88, 101, 242), (114, 137, 218))
                print(data["response"])
                
                if data.get("extra_data"):
                    if data["extra_data"].get("conversationId"):
                        conversation_id = data["extra_data"]["conversationId"]
                    if data["extra_data"].get("cookies"):
                        last_cookies = data["extra_data"]["cookies"]
                
                if data.get("images"):
                    Log.Blank()
                    with Log.Context("Images"):
                        Log.Info(f"Downloading {len(data['images'])} images...")
                    
                    saved = ImageDownloader.download(data["images"], cookies=last_cookies)
                    
                    if saved:
                        with Log.Context("Images"):
                            Log.Success(f"Saved {len(saved)} to ./images/")
                        for path in saved:
                            print(f"  → {path}")
                            
            elif "error" in data:
                with Log.Context("Error"):
                    Log.Error(data.get("message", str(data)))
                
        except KeyboardInterrupt:
            Log.Blank()
            with Log.Context("System"):
                Log.Warning("Interrupted")
            manager.print_stats()
            break
        except Exception as e:
            with Log.Context("Error"):
                Log.Error(str(e))
    
    manager.shutdown()


if __name__ == "__main__":
    import sys
    
    if "--openai" in sys.argv:
        from grok.openai_api import run_openai_server
        port = 8080
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        run_openai_server(port=port)
    elif "--genkey" in sys.argv:
        from grok.auth import get_key_manager
        name = "default"
        for i, arg in enumerate(sys.argv):
            if arg == "--name" and i + 1 < len(sys.argv):
                name = sys.argv[i + 1]
        key_manager = get_key_manager()
        key = key_manager.generate_key(name)
        print(f"\nGenerated API Key: {key}\n")
        print("Store this key securely - it won't be shown again!")
    else:
        api_thread = threading.Thread(target=start_api, daemon=True)
        api_thread.start()
        terminal_chat()
