"""Small dependency-free client for the Gemini APIs used by this extension.

It deliberately mirrors the subset of ``google-genai`` the extension used so the
worker can remain synchronous and concurrent without pulling in another HTTP stack.
"""

import json
import mimetypes
import re
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GeminiApiError(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(message)
        self.status = status
        self.code = status  # compatibility with google.genai.errors.ClientError
        self.body = body


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _camel(name):
    head, *tail = name.split("_")
    return head + "".join(x[:1].upper() + x[1:] for x in tail)


def _wire(value):
    if isinstance(value, dict):
        return {_camel(str(k)): _wire(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_wire(x) for x in value]
    return value


def _time(value):
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value


class GeminiObject(dict):
    """Dictionary response with the attribute access supplied by google-genai."""

    def __getattr__(self, name):
        return self.get(name)


def _object(value, key=None):
    if isinstance(value, dict):
        obj = GeminiObject()
        for k, v in value.items():
            sk = _snake(k)
            obj[sk] = _object(v, sk)
        if "candidates" in obj and "text" not in obj:
            parts = []
            for candidate in obj.get("candidates") or []:
                for part in (candidate.get("content") or {}).get("parts") or []:
                    if part.get("text"):
                        parts.append(part["text"])
            obj["text"] = "".join(parts)
        return obj
    if isinstance(value, list):
        return [_object(x, key) for x in value]
    if key and key.endswith("_time"):
        return _time(value)
    return value


class _Documents:
    def __init__(self, client):
        self.client = client

    def list(self, parent):
        documents, token = [], None
        while True:
            query = {"pageSize": 20}
            if token:
                query["pageToken"] = token
            page = self.client.request("GET", f"{parent}/documents", query=query)
            documents.extend(page.get("documents") or [])
            token = page.get("next_page_token")
            if not token:
                return documents

    def get(self, name):
        return self.client.request("GET", name)

    def delete(self, name, config=None, timeout=None):
        force = (config or {}).get("force", True)
        options = {"timeout": timeout} if timeout is not None else {}
        return self.client.request("DELETE", name, query={"force": str(force).lower()}, **options)


class _FileSearchStores:
    def __init__(self, client):
        self.client = client
        self.documents = _Documents(client)

    def create(self, config):
        return self.client.request("POST", "fileSearchStores", config)

    def get(self, name, timeout=None):
        options = {"timeout": timeout} if timeout is not None else {}
        return self.client.request("GET", name, **options)

    def delete(self, name, config=None):
        force = (config or {}).get("force", True)
        return self.client.request("DELETE", name, query={"force": str(force).lower()})

    def upload_to_file_search_store(self, file_search_store_name, file, config):
        return self.client.upload(file_search_store_name, file, config)


class _Operations:
    def __init__(self, client):
        self.client = client

    def get(self, operation):
        name = operation if isinstance(operation, str) else operation.get("name")
        if not name:
            raise ValueError("Gemini operation has no name")
        return self.client.request("GET", name)


class _Models:
    def __init__(self, client):
        self.client = client

    def generate_content(self, model, contents, config=None):
        body = {"contents": self._contents(contents), **(config or {})}
        return self.client.request("POST", f"models/{model}:generateContent", body)

    def generate_content_stream(self, model, contents, config=None):
        body = {"contents": self._contents(contents), **(config or {})}
        yield from self.client.stream(f"models/{model}:streamGenerateContent", body)

    @staticmethod
    def _contents(contents):
        if isinstance(contents, str):
            return [{"role": "user", "parts": [{"text": contents}]}]
        return contents


class GeminiClient:
    def __init__(self, api_key, api="https://generativelanguage.googleapis.com", timeout=600):
        self.api_key = api_key
        self.api = api.rstrip("/")
        self.timeout = timeout
        self.file_search_stores = _FileSearchStores(self)
        self.operations = _Operations(self)
        self.models = _Models(self)

    def url(self, path, query=None, upload=False):
        params = {"key": self.api_key, **(query or {})}
        prefix = "upload/" if upload else ""
        return f"{self.api}/{prefix}v1beta/{path}?{urlencode(params)}"

    def open(self, request, timeout=None):
        try:
            return urlopen(request, timeout=self.timeout if timeout is None else timeout)
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("error", {}).get("message")
            except (ValueError, AttributeError):
                message = None
            raise GeminiApiError(e.code, message or f"Gemini API failed with {e.code}: {body[:500]}", body) from e

    def request(self, method, path, body=None, query=None, timeout=None):
        data = None if body is None else json.dumps(_wire(body)).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(self.url(path, query), data=data, headers=headers, method=method)
        response = self.open(request) if timeout is None else self.open(request, timeout=timeout)
        with response:
            raw = response.read()
        return _object(json.loads(raw)) if raw else GeminiObject()

    def upload(self, store_name, file_path, config):
        with open(file_path, "rb") as f:
            content = f.read()
        mime_type = config.get("mime_type") or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        start_headers = {
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(content)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }
        start = Request(
            self.url(f"{store_name}:uploadToFileSearchStore", upload=True),
            data=json.dumps(_wire(config)).encode("utf-8"), headers=start_headers, method="POST")
        with self.open(start) as response:
            upload_url = response.headers.get("x-goog-upload-url")
        if not upload_url:
            raise GeminiApiError(502, "Gemini did not return an upload URL")
        final = Request(upload_url, data=content, headers={
            "Content-Type": mime_type,
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }, method="POST")
        with self.open(final) as response:
            raw = response.read()
        return _object(json.loads(raw))

    def stream(self, path, body):
        request = Request(self.url(path, {"alt": "sse"}),
                          data=json.dumps(_wire(body)).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                          method="POST")
        with self.open(request) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data and data != "[DONE]":
                    yield _object(json.loads(data))
