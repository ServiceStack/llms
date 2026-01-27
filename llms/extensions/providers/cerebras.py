def install_cerebras(ctx):
    from llms.main import OpenAiCompatible

    class CerebrasProvider(OpenAiCompatible):
        sdk = "@ai-sdk/cerebras"

        def __init__(self, **kwargs):
            if "api" not in kwargs:
                kwargs["api"] = "https://api.cerebras.ai/v1"
            super().__init__(**kwargs)

        async def chat(self, chat, context=None):
            # Cerebras only supports string content for text-only models
            clean_chat = chat.copy()
            clean_chat["messages"] = []
            for msg in chat.get("messages", []):
                new_msg = msg.copy()
                content = msg.get("content")
                if isinstance(content, list):
                    # Check if text only
                    is_text_only = True
                    text_parts = []
                    for part in content:
                        if part.get("type") != "text":
                            is_text_only = False
                            break
                        text_parts.append(part.get("text", ""))

                    if is_text_only:
                        new_msg["content"] = "".join(text_parts)
                clean_chat["messages"].append(new_msg)

            clean_chat.pop("modalities", None)
            return await super().chat(clean_chat, context)

    ctx.add_provider(CerebrasProvider)
