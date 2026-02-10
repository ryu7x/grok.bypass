# Grok Bypass API

A reverse-engineered API for [Grok](https://grok.com) that provides an **OpenAI-compatible** interface. No API keys from xAI needed — uses anonymous sessions with automatic session pooling, rate limit handling, and browser fingerprint rotation.

## Features

- **OpenAI-Compatible API** — Drop-in replacement for OpenAI's `/v1/chat/completions` and `/v1/images/generations`
- **Session Pooling** — Multiple browser sessions with automatic rotation and fingerprint randomization
- **Rate Limit Bypass** — Smart backoff, session recycling, and cooldown management
- **Image Generation** — Generate images via Grok and get direct CDN URLs
- **Terminal Chat** — Interactive CLI for chatting with Grok directly
- **Conversation Memory** — Continue multi-turn conversations via `conversation_id`
- **Anti-Bot Evasion** — Cloudflare cookie management, challenge solving, and browser impersonation

## Models

| Model | Mode | Description |
|---|---|---|
| `grok-3-auto` | Auto | Default, balanced mode |
| `grok-3-fast` | Fast | Faster responses, less rate limits |
| `grok-4` | Expert | Most capable, higher rate limit risk |
| `grok-4-mini-thinking-tahoe` | Thinking | Mini model with reasoning |

**OpenAI aliases** — `gpt-3.5-turbo`, `gpt-4` → `grok-3-auto` · `gpt-4-turbo`, `gpt-4o` → `grok-4`

## Quick Start

### Install

```bash
git clone https://github.com/your-repo/grok.bypass.git
cd grok.bypass
pip install -r requirements.txt
```

### Run

```bash
# Terminal chat + API server on port 6969
python main.py

# OpenAI-compatible server on port 8080
python main.py --openai

# Custom port
python main.py --openai --port 3000

# Generate an API key
python main.py --genkey
```

## API Endpoints

### Native API (port 6969)

#### Chat — `POST /ask`

```json
{
    "message": "Hello, what is quantum computing?",
    "model": "grok-3-auto",
    "conversation_id": null
}
```

**Response:**
```json
{
    "status": "success",
    "response": "Quantum computing is...",
    "images": [],
    "conversation_id": "abc-123-def"
}
```

#### Image Generation — `POST /generate`

```json
{
    "prompt": "a sunset over mountains"
}
```

**Response:**
```json
{
    "status": "success",
    "images": [
        "https://assets.grok.com/anon-users/.../image.jpg",
        "https://assets.grok.com/anon-users/.../image.jpg"
    ],
    "response": "I generated images with the prompt..."
}
```

#### Other Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/stats` | GET | Pool & request statistics |
| `/refresh` | POST | Force refresh all sessions |
| `/conversation/{id}` | GET | Check if a conversation exists |

---

### OpenAI-Compatible API (port 8080)

**Base URL:** `http://localhost:8080/v1`

All endpoints require an API key in the `Authorization` header:
```
Authorization: Bearer YOUR_API_KEY
```

#### Chat Completions — `POST /v1/chat/completions`

```json
{
    "model": "grok-4",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ],
    "stream": false
}
```

**Response:** Standard OpenAI chat completion format.

#### Image Generation — `POST /v1/images/generations`

```json
{
    "prompt": "a cyberpunk city at night",
    "n": 2
}
```

**Response:**
```json
{
    "created": 1700000000,
    "data": [
        {"url": "https://assets.grok.com/anon-users/.../image.jpg", "revised_prompt": "..."},
        {"url": "https://assets.grok.com/anon-users/.../image.jpg", "revised_prompt": "..."}
    ]
}
```

#### Other Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/models` | GET | List available models |
| `/v1/models/{id}` | GET | Get model details |
| `/v1/completions` | POST | Text completions |
| `/v1/keys/generate` | POST | Generate a new API key |

## API Key Management

```bash
# Generate a key via CLI
python main.py --genkey --name "my-bot"

# Generate via API (no auth required)
curl -X POST "http://localhost:8080/v1/keys/generate?name=my-bot"
```

Keys are stored in `keys.json` and persist across restarts.

## Terminal Chat Commands

| Command | Description |
|---|---|
| `stats` | Show request & session statistics |
| `refresh` | Refresh all sessions in the pool |
| `new` | Start a new conversation |
| `images` | Show image generation stats |
| `generate <prompt>` | Generate images from a prompt |
| `analyze <path> [prompt]` | Analyze an image file |
| `exit` / `quit` / `q` | Exit the chat |

## Deployment (Render)

1. Push to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `python main.py --openai`
5. Deploy

Your API will be available at: `https://your-app.onrender.com/v1`

## Use with Discord Bot

```python
GROK_API_BASE_URL = "https://your-app.onrender.com/v1"
GROK_API_KEY = "your-api-key"
GROK_MODEL = "grok-3-fast"  # recommended for bots

# Standard OpenAI-compatible request
payload = {
    "model": GROK_MODEL,
    "messages": [{"role": "user", "content": "Hello!"}]
}

response = requests.post(
    f"{GROK_API_BASE_URL}/chat/completions",
    json=payload,
    headers={"Authorization": f"Bearer {GROK_API_KEY}"}
)
```

## Project Structure

```
grok.bypass/
├── main.py              # Entry point — terminal chat + native API
├── requirements.txt     # Dependencies
├── keys.json            # API keys (auto-generated)
└── grok/
    ├── __init__.py      # Package exports
    ├── api.py           # Core Grok interaction — sessions, challenges, conversations
    ├── openai_api.py    # OpenAI-compatible API server
    ├── manager.py       # Request orchestration — retries, session selection
    ├── pool.py          # Session pooling — fingerprints, throttling, cookies
    ├── limiter.py       # Rate limiting — backoff, burst control, circuit breaker
    ├── base.py          # Header templates
    ├── auth.py          # API key management
    ├── logger.py        # Logging utilities
    ├── utils.py         # Helpers — image downloader, error handling
    ├── logic/
    │   ├── html.py      # HTML/JS parsing for challenges
    │   ├── secure.py    # Signature generation
    │   └── crypto.py    # Key generation & challenge signing
    └── mappings/
        ├── cookies.json # Cached Cloudflare cookies
        └── txid.json    # Transaction ID mappings
```

## Rate Limiting

The system handles rate limits at multiple levels:

| Layer | What it does |
|---|---|
| **Pool Throttle** | 0.6s minimum between requests per session |
| **Burst Control** | Max 8 requests per 10s window |
| **Session Rotation** | Switches to a fresh session on rate limit |
| **Exponential Backoff** | 0.5s base, up to 15s max on failures |
| **Cooldown** | 12-15s session cooldown after rate limit hit |
| **Circuit Breaker** | Opens after 5 consecutive failures, recovers after 60s |

## License

For educational purposes only.
