#!/usr/bin/env python3
"""Opt-in Gemini API smoke test. Creates and deletes its own temporary File Search store.

Requires GEMINI_API_KEY (or GOOGLE_API_KEY). Uses billable Gemini API requests.
Run from the repository root: .venv/bin/python scripts/check-google-tool-combination.py
"""

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llms.extensions.providers.google import install_google
from llms.main import get_client_timeout

BASE = "https://generativelanguage.googleapis.com"


def request(key, path, body=None, method="POST", headers=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(
        path if path.startswith("https://") else BASE + path,
        data=data,
        method=method,
        headers={"x-goog-api-key": key, "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = response.read()
            return (json.loads(payload) if payload else {}), response.headers
    except urllib.error.HTTPError as error:
        payload = json.load(error)
        raise RuntimeError(f"Gemini HTTP {error.code}: {payload.get('error', {}).get('message')}") from None


def upload_document(key, store, text):
    data = text.encode()
    _, headers = request(
        key,
        f"/upload/v1beta/{store}:uploadToFileSearchStore",
        {"displayName": "Verification document"},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": "text/plain",
        },
    )
    operation, _ = request(
        key,
        headers["X-Goog-Upload-URL"],
        data,
        headers={
            "Content-Type": "text/plain",
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
    )
    deadline = time.monotonic() + 120
    while not (operation.get("done") or operation.get("response") or operation.get("error")):
        if time.monotonic() >= deadline:
            raise TimeoutError("File Search document indexing timed out: " + json.dumps(operation))
        time.sleep(1)
        operation, _ = request(key, "/v1beta/" + operation["name"], method="GET")
    if operation.get("error"):
        raise RuntimeError("File Search indexing failed: " + json.dumps(operation["error"]))


async def check_model(provider, store, code, model, stream):
    file_search = {"type": "file_search", "file_search": {"file_search_store_names": [store]}}
    chat = {
        "model": model,
        "stream": stream,
        "max_completion_tokens": 4096,
        "thinkingConfig": {"thinkingLevel": "LOW"},
        "messages": [
            {
                "role": "user",
                "content": "Find the private launch code in the Verification document using File Search. "
                "Then call verify_code with that code. Finally report the code and the verification result.",
            }
        ],
        "tools": [
            file_search,
            {
                "type": "function",
                "function": {
                    "name": "verify_code",
                    "description": "Verify the private launch code found in the document.",
                    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                },
            },
        ],
    }
    verified = False
    builtin_parts = 0
    for _ in range(5):
        response = await provider.chat(chat)
        message = response["choices"][0]["message"]
        chat["messages"].append(message)
        parts = message["provider_metadata"]["google"]["content"]["parts"]
        builtin_parts += sum("toolCall" in part or "toolResponse" in part for part in parts)
        calls = message.get("tool_calls") or []
        if not calls:
            if not verified or not builtin_parts or code not in message.get("content", ""):
                raise AssertionError("Expected retrieval, successful custom verification, and code in final answer")
            combined_citations = bool(message.get("groundingMetadata"))
            break
        for call in calls:
            if call["function"]["name"] != "verify_code":
                raise AssertionError("Unexpected custom function")
            args = json.loads(call["function"]["arguments"])
            if args.get("code") != code:
                raise AssertionError("Model did not retrieve the private code")
            verified = True
            chat["messages"].append(
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"verified": True, "code": code})}
            )
    else:
        raise AssertionError("Model did not finish within five tool exchanges")

    baseline = await provider.chat(
        {
            "model": model,
            "stream": stream,
            "max_completion_tokens": 4096,
            "thinkingConfig": {"thinkingLevel": "LOW"},
            "tools": [file_search],
            "messages": [
                {
                    "role": "user",
                    "content": "According to the Verification document, what is the private launch code? Cite the document.",
                }
            ],
        }
    )
    plain = baseline["choices"][0]["message"]
    if code not in plain.get("content", ""):
        raise AssertionError("File Search-only baseline did not retrieve the private code")
    return {
        "model": model,
        "stream": stream,
        "verified": True,
        "builtinParts": builtin_parts,
        "combinedCitations": combined_citations,
        "fileSearchOnlyCitations": bool(plain.get("groundingMetadata")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+", default=["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]
    )
    args = parser.parse_args()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        parser.error("Set GEMINI_API_KEY or GOOGLE_API_KEY")
    registered = []
    install_google(
        SimpleNamespace(
            add_provider=registered.append,
            log=lambda *_: None,
            dbg=lambda *_: None,
            err=lambda *_: None,
            log_json=lambda value: value,
            debug=False,
            MOCK=False,
            get_client_timeout=get_client_timeout,
            should_cancel_thread=lambda _: False,
        )
    )
    provider = registered[0](id="google", api_key=key)
    store, _ = request(
        key, "/v1beta/fileSearchStores", {"displayName": "llms.py temporary tool-combination verification"}
    )
    name = store["name"]
    failures = 0
    try:
        code = "ORCHID-" + uuid.uuid4().hex[:12]
        upload_document(key, name, f"Verification document\nThe private launch code is {code}.\n")
        for model in args.models:
            for stream in (False, True):
                try:
                    result = asyncio.run(check_model(provider, name, code, model, stream))
                except Exception as error:
                    failures += 1
                    result = {"model": model, "stream": stream, "error": str(error)}
                print(json.dumps(result), flush=True)
    finally:
        request(key, f"/v1beta/{name}?force=true", method="DELETE")
        print(json.dumps({"temporaryStoreDeleted": True}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
