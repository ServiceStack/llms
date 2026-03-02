def install_fireworks(ctx):
    from llms.main import OpenAiCompatible

    class FireworksProvider(OpenAiCompatible):
        sdk = "@fireworks/ai-sdk-provider"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        async def process_chat(self, chat, provider_id=None):
            ret = await super().process_chat(chat, provider_id)
            chat.pop("modalities", None)
            chat.pop("enable_thinking", None)
            messages = chat.get("messages", []).copy()
            for message in messages:
                # fireworks doesn't support timestamp, reasoning, refusal
                message.pop("timestamp", None)
                message.pop("reasoning", None)
                message.pop("refusal", None)
            ret["messages"] = messages
            return ret

    ctx.add_provider(FireworksProvider)
