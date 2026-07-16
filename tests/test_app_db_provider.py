import os
import shutil
import tempfile
import unittest

from llms.extensions.app import install


class MockContext:
    def __init__(self, db_path):
        self.db_path = db_path
        self.chat_request_filters = []
        self.chat_response_filters = []
        self.config = {"database": db_path, "defaults": {}}
        self.debug = True
        self.threads = None

    def get_config(self):
        return self.config

    def get_home_path(self, name=""):
        return self.db_path

    def register_chat_request_filter(self, fn):
        self.chat_request_filters.append(fn)

    def register_chat_response_filter(self, fn):
        self.chat_response_filters.append(fn)

    def register_chat_tool_filter(self, fn):
        pass

    def register_chat_status_filter(self, fn):
        pass

    def register_chat_error_filter(self, fn):
        pass

    def get_username(self, req):
        return "test_user"

    def chat_to_system_prompt(self, chat):
        return "System prompt"

    def last_user_prompt(self, chat):
        return "Hello"

    def next_loading_message(self):
        return "Loading..."

    def cache_message_inline_data(self, msg, context=None):
        pass

    def dbg(self, msg):
        pass

    def err(self, msg, err):
        pass

    def log(self, msg):
        pass

    def add_get(self, *args, **kwargs):
        pass

    def add_post(self, *args, **kwargs):
        pass

    def add_delete(self, *args, **kwargs):
        pass

    def add_put(self, *args, **kwargs):
        pass

    def add_patch(self, *args, **kwargs):
        pass

    def add_importmaps(self, *args, **kwargs):
        pass

    def add_index_header(self, *args, **kwargs):
        pass

    def add_index_footer(self, *args, **kwargs):
        pass

    def get_user_path(self, user=None):
        return "/tmp/llms_test_user"

    def chat_response_to_message(self, response):
        return {"role": "assistant", "content": "Hi!"}

class TestAppDbProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.ctx = MockContext(self.db_path)

        # We need to mock g_db global in the extensions.app module
        # So we import and override it.
        import llms.extensions.app as app_mod
        self.original_g_db = app_mod.g_db

        # Let install create the AppDB instance
        install(self.ctx)
        self.app_db = app_mod.g_db

    def tearDown(self):
        self.app_db.close()
        import llms.extensions.app as app_mod
        app_mod.g_db = self.original_g_db
        shutil.rmtree(self.temp_dir)

    async def test_chat_filters_save_and_update_provider(self):
        chat = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        context = {
            "chat": chat,
            "user": "test_user",
            "provider": "test-provider",
            "modelInfo": {"id": "test-model", "name": "Test Model", "cost": {"input": 0, "output": 0}}
        }

        # 1. Run the request filter (simulating start of chat)
        for filter_fn in self.ctx.chat_request_filters:
            await filter_fn(chat, context)

        thread_id = context.get("threadId")
        self.assertIsNotNone(thread_id)

        # Verify thread was created with provider
        thread = self.app_db.get_thread(thread_id, user="test_user")
        self.assertIsNotNone(thread)
        self.assertEqual(thread.get("provider"), "test-provider")
        self.assertEqual(thread.get("model"), "test-model")

        # 2. Run the response filter (simulating completion of chat with a different provider if retried/fallback)
        context["provider"] = "fallback-provider"
        response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}
        }
        for filter_fn in self.ctx.chat_response_filters:
            await filter_fn(response, context)

        # Verify thread was updated with the actual completing provider
        updated_thread = self.app_db.get_thread(thread_id, user="test_user")
        self.assertEqual(updated_thread.get("provider"), "fallback-provider")

if __name__ == "__main__":
    unittest.main()
