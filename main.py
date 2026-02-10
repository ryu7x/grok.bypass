import threading
from fastapi      import FastAPI, HTTPException
from pydantic     import BaseModel
from typing       import Optional, List
from grok         import Log, GrokManager, ImageDownloader
from uvicorn      import run


CDN_BASE = "https://assets.grok.com/"

app = FastAPI(title="Grok API", version="2.0.0")
manager = GrokManager(pool_size=8, max_workers=4, max_retries=4, base_delay=0.8)


def _to_cdn_urls(image_paths: list) -> list:
    """Convert Grok image paths to full CDN URLs."""
    return [
        img if img.startswith("http") else f"{CDN_BASE}{img}"
        for img in (image_paths or [])
    ]


class ConversationRequest(BaseModel):
    message: str
    model: str = "grok-3-auto"
    extra_data: Optional[dict] = None
    conversation_id: Optional[str] = None


class ImageRequest(BaseModel):
    prompt: str
    model: str = "grok-3-auto"


@app.post("/ask")
async def create_conversation(request: ConversationRequest):
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
        
        return {
            "status": "success",
            "response": answer.get("response"),
            "images": _to_cdn_urls(answer.get("images")),
            "conversation_id": answer.get("extra_data", {}).get("conversationId")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_image(request: ImageRequest):
    try:
        answer = manager.generate_images(prompt=request.prompt, model=request.model)
        if "error" in answer:
            raise HTTPException(status_code=500, detail=answer.get("message"))
        
        return {
            "status": "success",
            "images": _to_cdn_urls(answer.get("images")),
            "response": answer.get("response")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats():
    return manager.get_stats()


@app.post("/refresh")
async def refresh_pool():
    manager.refresh_pool()
    return {"status": "success"}


@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str):
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