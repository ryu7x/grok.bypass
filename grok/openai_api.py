from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union
from time import time
from secrets import token_hex
from grok import GrokManager, Log
from grok.auth import get_key_manager
import json


CDN_BASE = "https://assets.grok.com/"

app = FastAPI(title="Grok OpenAI Compatible API", version="1.0.0")
manager = GrokManager(pool_size=8, max_workers=4, max_retries=4, base_delay=0.8)


MODELS = {
    "grok-3-auto": {"id": "grok-3-auto", "object": "model", "created": 1700000000, "owned_by": "xai"},
    "grok-3-fast": {"id": "grok-3-fast", "object": "model", "created": 1700000000, "owned_by": "xai"},
    "grok-4": {"id": "grok-4", "object": "model", "created": 1700000000, "owned_by": "xai"},
    "gpt-3.5-turbo": {"id": "gpt-3.5-turbo", "object": "model", "created": 1700000000, "owned_by": "xai", "alias": "grok-3-auto"},
    "gpt-4": {"id": "gpt-4", "object": "model", "created": 1700000000, "owned_by": "xai", "alias": "grok-3-auto"},
    "gpt-4-turbo": {"id": "gpt-4-turbo", "object": "model", "created": 1700000000, "owned_by": "xai", "alias": "grok-4"},
    "gpt-4o": {"id": "gpt-4o", "object": "model", "created": 1700000000, "owned_by": "xai", "alias": "grok-4"},
}


class Message(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "grok-3-auto"
    messages: List[Message]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None


class CompletionRequest(BaseModel):
    model: str = "grok-3-auto"
    prompt: Union[str, List[str]]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = 256
    stream: Optional[bool] = False


class ImageGenerationRequest(BaseModel):
    model: str = "dall-e-3"
    prompt: str
    n: Optional[int] = 2
    size: Optional[str] = "1024x1024"
    response_format: Optional[str] = "url"


def verify_api_key(authorization: Optional[str] = Header(None)) -> bool:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": {"message": "Missing API key", "type": "invalid_request_error", "code": "missing_api_key"}})
    
    key_manager = get_key_manager()
    if not key_manager.validate_key(authorization):
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}})
    
    return True


def resolve_model(model: str) -> str:
    if model in MODELS and "alias" in MODELS[model]:
        return MODELS[model]["alias"]
    return model if model in ["grok-3-auto", "grok-3-fast", "grok-4"] else "grok-3-auto"


def create_chat_completion_response(response: str, model: str, stream: bool = False) -> dict:
    completion_id = f"chatcmpl-{token_hex(12)}"
    created = int(time())
    
    if stream:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": response},
                "finish_reason": None
            }]
        }
    
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": len(response) // 4,
            "completion_tokens": len(response) // 4,
            "total_tokens": len(response) // 2
        }
    }


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    return {
        "object": "list",
        "data": list(MODELS.values())
    }


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    if model_id not in MODELS:
        raise HTTPException(status_code=404, detail={"error": {"message": f"Model {model_id} not found", "type": "invalid_request_error"}})
    return MODELS[model_id]


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    
    model = resolve_model(request.model)
    
    messages_text = "\n".join([f"{m.role}: {m.content}" for m in request.messages])
    last_message = request.messages[-1].content if request.messages else ""
    
    context = ""
    if len(request.messages) > 1:
        context = "\n".join([f"{m.role}: {m.content}" for m in request.messages[:-1]])
        full_message = f"Context:\n{context}\n\nUser: {last_message}"
    else:
        full_message = last_message
    
    result = manager.ask(full_message, model=model)
    
    if "error" in result:
        error_type = result.get("error", "unknown")
        msg = result.get("message", "Unknown error")
        if error_type == "ratelimit":
            raise HTTPException(status_code=429, detail={"error": {"message": msg, "type": "rate_limit_error"}})
        elif error_type == "heavy_usage":
            raise HTTPException(status_code=503, detail={"error": {"message": msg, "type": "server_overloaded"}})
        else:
            raise HTTPException(status_code=500, detail={"error": {"message": msg, "type": "server_error"}})
    
    response_text = result.get("response", "")
    
    # Append image URLs directly into the response text as markdown
    images = result.get("images", [])
    if images:
        image_md = "\n\n"
        for i, img_path in enumerate(images):
            url = f"https://assets.grok.com/{img_path}" if not img_path.startswith("http") else img_path
            image_md += f"![image_{i+1}]({url})\n"
        response_text += image_md
    
    if request.stream:
        async def generate():
            completion_id = f"chatcmpl-{token_hex(12)}"
            created = int(time())
            
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            
            chunk["choices"][0]["delta"] = {"content": response_text}
            yield f"data: {json.dumps(chunk)}\n\n"
            
            chunk["choices"][0]["delta"] = {}
            chunk["choices"][0]["finish_reason"] = "stop"
            yield f"data: {json.dumps(chunk)}\n\n"
            
            yield "data: [DONE]\n\n"
        
        return StreamingResponse(generate(), media_type="text/event-stream")
    
    return create_chat_completion_response(response_text, model)


@app.post("/v1/completions")
async def completions(request: CompletionRequest, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    
    model = resolve_model(request.model)
    prompt = request.prompt if isinstance(request.prompt, str) else request.prompt[0]
    
    result = manager.ask(prompt, model=model)
    
    if "error" in result:
        raise HTTPException(status_code=500, detail={"error": {"message": result.get("message", "Unknown error"), "type": "server_error"}})
    
    response_text = result.get("response", "")
    
    return {
        "id": f"cmpl-{token_hex(12)}",
        "object": "text_completion",
        "created": int(time()),
        "model": model,
        "choices": [{
            "text": response_text,
            "index": 0,
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(response_text) // 4,
            "total_tokens": (len(prompt) + len(response_text)) // 4
        }
    }


@app.post("/v1/images/generations")
async def image_generations(request: ImageGenerationRequest, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    
    result = manager.generate_images(request.prompt)
    
    if "error" in result:
        error_type = result.get("error", "unknown")
        msg = result.get("message", "Unknown error")
        if error_type == "ratelimit":
            raise HTTPException(status_code=429, detail={"error": {"message": msg, "type": "rate_limit_error"}})
        elif error_type == "heavy_usage":
            raise HTTPException(status_code=503, detail={"error": {"message": msg, "type": "server_overloaded"}})
        else:
            raise HTTPException(status_code=500, detail={"error": {"message": msg, "type": "server_error"}})
    
    images = result.get("images", [])
    image_data = []
    for img_path in images:
        url = f"{CDN_BASE}{img_path}" if not img_path.startswith("http") else img_path
        image_data.append({"url": url, "revised_prompt": request.prompt})
    
    return {
        "created": int(time()),
        "data": image_data
    }


@app.post("/v1/embeddings")
async def embeddings(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    raise HTTPException(status_code=501, detail={"error": {"message": "Embeddings are not supported by Grok", "type": "not_implemented"}})


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    raise HTTPException(status_code=501, detail={"error": {"message": "Audio transcriptions are not supported by Grok", "type": "not_implemented"}})


@app.get("/v1/keys")
async def list_keys(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    key_manager = get_key_manager()
    return {"keys": key_manager.list_keys()}


@app.post("/v1/keys/generate")
async def generate_key(name: str = "default"):
    key_manager = get_key_manager()
    key = key_manager.generate_key(name)
    return {"key": key, "name": name, "message": "Store this key securely, it won't be shown again"}


@app.get("/")
async def root():
    return {
        "message": "Grok OpenAI Compatible API",
        "version": "1.0.0",
        "endpoints": [
            "/v1/chat/completions",
            "/v1/completions",
            "/v1/images/generations",
            "/v1/models",
            "/v1/keys/generate"
        ]
    }


def run_openai_server(host: str = "0.0.0.0", port: int = 8080):
    from uvicorn import run as uvicorn_run
    
    Log.Section("GROK OPENAI API", start_rgb=(88, 101, 242), end_rgb=(114, 137, 218))
    Log.Blank()
    
    key_manager = get_key_manager()
    if key_manager.get_key_count() == 0:
        key = key_manager.generate_key("default")
        with Log.Context("Auth"):
            Log.Info(f"First run - generated API key: {key}")
    
    with Log.Context("Server"):
        Log.Info(f"Starting on http://{host}:{port}")
        Log.Info("Endpoints: /v1/chat/completions, /v1/images/generations, /v1/models")
    
    uvicorn_run(app, host=host, port=port, log_level="error")
