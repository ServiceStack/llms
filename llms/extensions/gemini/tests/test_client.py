import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gemini_rest_client", os.path.join(HERE, "..", "client.py"))
client_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_mod)


class Response:
    def __init__(self, body=b"{}", headers=None, lines=None):
        self.body = body
        self.headers = headers or {}
        self.lines = lines

    def read(self):
        return self.body

    def __iter__(self):
        return iter(self.lines or [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class RecordingClient(client_mod.GeminiClient):
    def __init__(self, responses):
        super().__init__("secret", api="https://example.test")
        self.responses = iter(responses)
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return next(self.responses)


class TestGeminiClient(unittest.TestCase):
    def test_deletion_timeout_does_not_change_upload_timeout(self):
        client = client_mod.GeminiClient("secret", api="https://example.test")
        with patch.object(client_mod, "urlopen", return_value=Response()) as open_request:
            client.file_search_stores.documents.delete("documents/1", timeout=60)
            self.assertEqual(open_request.call_args.kwargs["timeout"], 60)
            client.file_search_stores.get("fileSearchStores/1", timeout=30)
            self.assertEqual(open_request.call_args.kwargs["timeout"], 30)
            client.file_search_stores.create({"displayName": "Docs"})
            self.assertEqual(open_request.call_args.kwargs["timeout"], 600)

    def test_request_converts_wire_and_response_names(self):
        response = Response(json.dumps({
            "name": "fileSearchStores/1", "displayName": "Docs", "activeDocumentsCount": 2
        }).encode())
        client = RecordingClient([response])
        store = client.file_search_stores.create({"display_name": "Docs"})
        self.assertEqual(json.loads(client.requests[0].data), {"displayName": "Docs"})
        self.assertEqual(store.display_name, "Docs")
        self.assertEqual(store.active_documents_count, 2)

    def test_documents_list_follows_page_token(self):
        client = RecordingClient([
            Response(b'{"documents":[{"name":"documents/1"}],"nextPageToken":"next"}'),
            Response(b'{"documents":[{"name":"documents/2"}]}'),
        ])
        docs = client.file_search_stores.documents.list("fileSearchStores/1")
        self.assertEqual([x.name for x in docs], ["documents/1", "documents/2"])
        self.assertIn("pageToken=next", client.requests[1].full_url)

    def test_upload_uses_resumable_protocol(self):
        client = RecordingClient([
            Response(headers={"x-goog-upload-url": "https://upload.test/session"}),
            Response(b'{"name":"operations/1","done":false}'),
        ])
        with tempfile.NamedTemporaryFile(suffix=".md") as f:
            f.write(b"hello")
            f.flush()
            op = client.file_search_stores.upload_to_file_search_store(
                "fileSearchStores/1", f.name,
                {"display_name": "hello.md", "custom_metadata": [
                    {"key": "versions", "string_list_value": {"values": ["v8"]}}
                ]})
        self.assertEqual(op.name, "operations/1")
        self.assertEqual(client.requests[0].get_header("X-goog-upload-command"), "start")
        self.assertEqual(client.requests[1].full_url, "https://upload.test/session")
        self.assertEqual(client.requests[1].data, b"hello")

    def test_stream_decodes_sse_and_text(self):
        body = {"candidates": [{"content": {"parts": [{"text": "Hi"}]}}]}
        client = RecordingClient([Response(lines=[f"data: {json.dumps(body)}\n".encode(), b"data: [DONE]\n"])])
        chunks = list(client.models.generate_content_stream("gemini-flash-latest", "Hello"))
        self.assertEqual(chunks[0].text, "Hi")
        sent = json.loads(client.requests[0].data)
        self.assertEqual(sent["contents"][0]["parts"][0]["text"], "Hello")


if __name__ == "__main__":
    unittest.main()
