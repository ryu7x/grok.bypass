from grok        import Log, Run, Utils, Parser, Signature, Anon, Headers
from curl_cffi   import requests, CurlMime
from dataclasses import dataclass, field
from bs4         import BeautifulSoup
from json        import dumps, loads
from secrets     import token_hex
from uuid        import uuid4
import re

@dataclass
class Models:
    models: dict[str, list[str]] = field(default_factory=lambda: {
        "grok-3-auto": ["MODEL_MODE_AUTO", "auto"],
        "grok-3-fast": ["MODEL_MODE_FAST", "fast"],
        "grok-4": ["MODEL_MODE_EXPERT", "expert"],
        "grok-4-mini-thinking-tahoe": ["MODEL_MODE_GROK_4_MINI_THINKING", "grok-4-mini-thinking"]
    })

    def get_model_mode(self, model: str, index: int) -> str:
        return self.models.get(model, ["MODEL_MODE_AUTO", "auto"])[index]

_Models = Models()

class Grok:
    
    
    def __init__(self, model: str = "grok-3-auto") -> None:
        self.session: requests.session.Session = requests.Session(impersonate="chrome136", default_headers=False)
        self.headers: Headers = Headers()
        
        self.model_mode: str = _Models.get_model_mode(model, 0)
        self.model: str = model
        self.mode: str = _Models.get_model_mode(model, 1)
        self.c_run: int = 0
        self.keys: dict = Anon.generate_keys()
    
    @staticmethod
    def _clean_response(text: str) -> str:
        if not text:
            return text
        text = re.sub(r'<grok:render[^>]*>[\s\S]*?</grok:render>', '', text)
        text = re.sub(r'<grok:[a-zA-Z_]+[^>]*/>', '', text)
        text = re.sub(r'<grok:[a-zA-Z_]+[^>]*>[\s\S]*?</grok:[a-zA-Z_]+>', '', text)
        text = re.sub(r'<argument[^>]*>[^<]*</argument>', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    
    def _load(self, extra_data: dict = None) -> None:
        
        if not extra_data:
            self.session.headers = self.headers.LOAD
            load_site: requests.models.Response = self.session.get('https://grok.com/c', timeout=30)
            self.session.cookies.update(load_site.cookies)
            
            scripts: list = [s['src'] for s in BeautifulSoup(load_site.text, 'html.parser').find_all('script', src=True) if s['src'].startswith('/_next/static/chunks/')]

            self.actions, self.xsid_script = Parser.parse_grok(scripts)
            
            self.baggage: str = Utils.between(load_site.text, '<meta name="baggage" content="', '"')
            self.sentry_trace: str = Utils.between(load_site.text, '<meta name="sentry-trace" content="', '-')
        else:
            self.session.cookies.update(extra_data["cookies"])

            self.actions: list = extra_data["actions"]
            self.xsid_script: list =  extra_data["xsid_script"]
            self.baggage: str = extra_data["baggage"]
            self.sentry_trace: str = extra_data["sentry_trace"]
            
    
    def c_request(self, next_action: str) -> None:
        
        self.session.headers = self.headers.C_REQUEST
        self.session.headers.update({
            'baggage': self.baggage,
            'next-action': next_action,
            'sentry-trace': f'{self.sentry_trace}-{str(uuid4()).replace("-", "")[:16]}-0',
        })
        self.session.headers = Headers.fix_order(self.session.headers, self.headers.C_REQUEST)
        
        if self.c_run == 0:
            self.session.headers.pop("content-type")
            
            mime = CurlMime()
            mime.addpart(name="1", data=bytes(self.keys["userPublicKey"]), filename="blob", content_type="application/octet-stream")
            mime.addpart(name="0", filename=None, data='[{"userPublicKey":"$o1"}]')
            
            c_request: requests.models.Response = self.session.post("https://grok.com/c", multipart=mime, timeout=30)
            self.session.cookies.update(c_request.cookies)
            
            self.anon_user: str = Utils.between(c_request.text, '{"anonUserId":"', '"')
            self.c_run += 1
            
        else:
            
            match self.c_run:
                case 1:
                    data: str = dumps([{"anonUserId":self.anon_user}])
                case 2:
                    data: str = dumps([{"anonUserId":self.anon_user,**self.challenge_dict}])
            
            c_request: requests.models.Response = self.session.post('https://grok.com/c', data=data, timeout=30)
            self.session.cookies.update(c_request.cookies)

            match self.c_run:
                case 1:
                    start_idx = c_request.content.hex().find("3a6f38362c")
                    if start_idx != -1:
                        start_idx += len("3a6f38362c")
                        end_idx = c_request.content.hex().find("313a", start_idx)
                        if end_idx != -1:
                            challenge_hex = c_request.content.hex()[start_idx:end_idx]
                            challenge_bytes = bytes.fromhex(challenge_hex)

                    self.challenge_dict: dict = Anon.sign_challenge(challenge_bytes, self.keys["privateKey"])
                case 2:
                    self.verification_token, self.anim = Parser.get_anim(c_request.text, "grok-site-verification")
                    self.svg_data, self.numbers = Parser.parse_values(c_request.text, self.anim, self.xsid_script)
                    
            self.c_run += 1
    
    def upload_image(self, image_path: str) -> dict:
        from pathlib import Path
        import base64
        import mimetypes
        
        path = Path(image_path)
        if not path.exists():
            return {"error": "file_not_found", "message": f"File not found: {image_path}"}
        
        mime_type, _ = mimetypes.guess_type(str(path))
        if not mime_type or not mime_type.startswith("image/"):
            return {"error": "invalid_file", "message": "File must be an image"}
        
        with open(path, "rb") as f:
            image_data = f.read()
        
        self._load()
        self.c_request(self.actions[0])
        self.c_request(self.actions[1])
        self.c_request(self.actions[2])
        
        self.session.headers = self.headers.CONVERSATION
        self.session.headers.update({
            'baggage': self.baggage,
            'sentry-trace': f'{self.sentry_trace}-{str(uuid4()).replace("-", "")[:16]}-0',
            'x-xai-request-id': str(uuid4()),
            'traceparent': f"00-{token_hex(16)}-{token_hex(8)}-00"
        })
        
        upload_url = "https://grok.com/rest/app-chat/upload-image"
        
        mime = CurlMime()
        mime.addpart(
            name="image",
            content_type=mime_type,
            filename=path.name,
            data=image_data
        )
        
        try:
            response = self.session.post(upload_url, multipart=mime, timeout=60)
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "image_url": data.get("url", data.get("imageUrl")),
                    "image_id": data.get("id", data.get("imageId")),
                    "data": data
                }
            else:
                return {"error": "upload_failed", "message": f"Upload failed: {response.status_code}"}
        except Exception as e:
            return {"error": "exception", "message": str(e)}
    def start_convo(self, message: str, extra_data: dict = None) -> dict:
        
        if not extra_data:
            self._load()
            self.c_request(self.actions[0])
            self.c_request(self.actions[1])
            self.c_request(self.actions[2])
            xsid: str = Signature.generate_sign('/rest/app-chat/conversations/new', 'POST', self.verification_token, self.svg_data, self.numbers)
        else:
            self._load(extra_data)
            self.c_run: int = 1
            self.anon_user: str = extra_data["anon_user"]
            self.keys["privateKey"] = extra_data["privateKey"]
            self.c_request(self.actions[1])
            self.c_request(self.actions[2])
            xsid: str = Signature.generate_sign(f'/rest/app-chat/conversations/{extra_data["conversationId"]}/responses', 'POST', self.verification_token, self.svg_data, self.numbers)

        self.session.headers = self.headers.CONVERSATION
        self.session.headers.update({
            'baggage': self.baggage,
            'sentry-trace': f'{self.sentry_trace}-{str(uuid4()).replace("-", "")[:16]}-0',
            'x-statsig-id': xsid,
            'x-xai-request-id': str(uuid4()),
            'traceparent': f"00-{token_hex(16)}-{token_hex(8)}-00"
        })
        self.session.headers = Headers.fix_order(self.session.headers, self.headers.CONVERSATION)
        
        if not extra_data:
            conversation_data: dict = {
                'temporary': False,
                'modelName': self.model,
                'message': message,
                'fileAttachments': [],
                'imageAttachments': [],
                'disableSearch': False,
                'enableImageGeneration': True,
                'returnImageBytes': False,
                'returnRawGrokInXaiRequest': False,
                'enableImageStreaming': True,
                'imageGenerationCount': 2,
                'forceConcise': False,
                'toolOverrides': {},
                'enableSideBySide': True,
                'sendFinalMetadata': True,
                'isReasoning': False,
                'webpageUrls': [],
                'disableTextFollowUps': False,
                'responseMetadata': {
                    'requestModelDetails': {
                        'modelId': self.model,
                    },
                },
                'disableMemory': False,
                'forceSideBySide': False,
                'modelMode': self.model_mode,
                'isAsyncChat': False,
            }
            
            convo_request: requests.models.Response = self.session.post('https://grok.com/rest/app-chat/conversations/new', json=conversation_data, timeout=120)
            
            if "modelResponse" in convo_request.text:
                response = conversation_id = parent_response = image_urls = None
                stream_response: list = []
                
                for response_dict in convo_request.text.strip().split('\n'):  
                    data: dict = loads(response_dict)

                    token: str = data.get('result', {}).get('response', {}).get('token')
                    if token:
                        stream_response.append(token)
                        
                    if not response and data.get('result', {}).get('response', {}).get('modelResponse', {}).get('message'):
                        response: str = data['result']['response']['modelResponse']['message']

                    if not conversation_id and data.get('result', {}).get('conversation', {}).get('conversationId'):
                        conversation_id: str = data['result']['conversation']['conversationId']

                    if not parent_response and data.get('result', {}).get('response', {}).get('modelResponse', {}).get('responseId'):
                        parent_response: str = data['result']['response']['modelResponse']['responseId']
                    
                    if not image_urls and data.get('result', {}).get('response', {}).get('modelResponse', {}).get('generatedImageUrls', {}):
                        image_urls: list = data['result']['response']['modelResponse']['generatedImageUrls']
                    
                
                return {
                    "response": self._clean_response(response),
                    "stream_response": stream_response,
                    "images": image_urls,
                    "extra_data": {
                        "anon_user": self.anon_user,
                        "cookies": self.session.cookies.get_dict(),
                        "actions": self.actions,
                        "xsid_script": self.xsid_script,
                        "baggage": self.baggage,
                        "sentry_trace": self.sentry_trace,
                        "conversationId": conversation_id,
                        "parentResponseId": parent_response,
                        "privateKey": self.keys["privateKey"]
                    }
                }
            else:
                error_info = self._parse_error(convo_request.text)
                return error_info
        else:
            conversation_data: dict = {
                'message': message,
                'modelName': self.model,
                'parentResponseId': extra_data["parentResponseId"],
                'disableSearch': False,
                'enableImageGeneration': True,
                'imageAttachments': [],
                'returnImageBytes': False,
                'returnRawGrokInXaiRequest': False,
                'fileAttachments': [],
                'enableImageStreaming': True,
                'imageGenerationCount': 2,
                'forceConcise': False,
                'toolOverrides': {},
                'enableSideBySide': True,
                'sendFinalMetadata': True,
                'customPersonality': '',
                'isReasoning': False,
                'webpageUrls': [],
                'metadata': {
                    'requestModelDetails': {
                        'modelId': self.model,
                    },
                    'request_metadata': {
                        'model': self.model,
                        'mode': self.mode,
                    },
                },
                'disableTextFollowUps': False,
                'disableArtifact': False,
                'isFromGrokFiles': False,
                'disableMemory': False,
                'forceSideBySide': False,
                'modelMode': self.model_mode,
                'isAsyncChat': False,
                'skipCancelCurrentInflightRequests': False,
                'isRegenRequest': False,
            }

            convo_request: requests.models.Response = self.session.post(f'https://grok.com/rest/app-chat/conversations/{extra_data["conversationId"]}/responses', json=conversation_data, timeout=120)

            if "modelResponse" in convo_request.text:
                response = conversation_id = parent_response = image_urls = None
                stream_response: list = []
                
                for response_dict in convo_request.text.strip().split('\n'):
                    data: dict = loads(response_dict)

                    token: str = data.get('result', {}).get('token')
                    if token:
                        stream_response.append(token)
                        
                    if not response and data.get('result', {}).get('modelResponse', {}).get('message'):
                        response: str = data['result']['modelResponse']['message']

                    if not parent_response and data.get('result', {}).get('modelResponse', {}).get('responseId'):
                        parent_response: str = data['result']['modelResponse']['responseId']
                        
                    if not image_urls and data.get('result', {}).get('modelResponse', {}).get('generatedImageUrls', {}):
                        image_urls: list = data['result']['modelResponse']['generatedImageUrls']
                
                return {
                    "response": self._clean_response(response),
                    "stream_response": stream_response,
                    "images": image_urls,
                    "extra_data": {
                        "anon_user": self.anon_user,
                        "cookies": self.session.cookies.get_dict(),
                        "actions": self.actions,
                        "xsid_script": self.xsid_script,
                        "baggage": self.baggage,
                        "sentry_trace": self.sentry_trace,
                        "conversationId": extra_data["conversationId"],
                        "parentResponseId": parent_response,
                        "privateKey": self.keys["privateKey"]
                    }
                }
            else:
                error_info = self._parse_error(convo_request.text)
                return error_info

    def _parse_error(self, response_text: str) -> dict:
        if 'rejected by anti-bot rules' in response_text:
            return {"error": "antibot", "message": "Anti-bot triggered", "retry": True}
        elif 'Too many requests' in response_text or 'code":8' in response_text:
            return {"error": "ratelimit", "message": "Rate limited", "retry": True}
        elif "Grok is under heavy usage" in response_text:
            return {"error": "heavy_usage", "message": "Heavy server load", "retry": True}
        else:
            return {"error": "unknown", "message": response_text, "retry": False}
