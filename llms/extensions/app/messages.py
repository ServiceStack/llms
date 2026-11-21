"""Client history projections and reconciliation with authoritative provider history."""

import copy


def client_message(message):
    """Remove server-only replay state without changing visible content or citations."""
    message = copy.deepcopy(message)
    message.pop("provider_metadata", None)
    for value in [message, *(message.get("tool_calls") or [])]:
        extra = value.get("extra_content") or {}
        for obj in (value, value.get("function"), extra.get("google")):
            if isinstance(obj, dict):
                obj.pop("thoughtSignature", None)
                obj.pop("thought_signature", None)
        if extra and extra.get("google") == {}:
            extra.pop("google")
            if not extra:
                value.pop("extra_content")
    return message


def client_thread(thread):
    """Project only known message fields; arbitrary tool results remain untouched."""
    thread = copy.deepcopy(thread)
    if not thread:
        return thread
    for key in ("messages", "toolHistory"):
        if isinstance(thread.get(key), list):
            thread[key] = [client_message(m) for m in thread[key]]
    if isinstance(thread.get("streamingMessage"), dict):
        thread["streamingMessage"] = client_message(thread["streamingMessage"])
    response = thread.get("providerResponse")
    if isinstance(response, dict):
        for choice in response.get("choices", []):
            if isinstance(choice.get("message"), dict):
                choice["message"] = client_message(choice["message"])
        if isinstance(response.get("tool_history"), list):
            response["tool_history"] = [client_message(m) for m in response["tool_history"]]
    return thread


def reconcile_client_messages(stored, incoming, rewrite=False, keep_prefix=False):
    """Window echoes append to server history; explicit branches hydrate signed survivors."""

    def clean(message):
        message = copy.deepcopy(message)
        message.pop("_sequence", None)
        message.pop("streaming", None)
        return message

    by_timestamp = {m.get("timestamp"): i for i, m in enumerate(stored) if m.get("timestamp") is not None}
    by_sequence = {m.get("_sequence"): i for i, m in enumerate(stored) if m.get("_sequence") is not None}

    def index(message):
        if message.get("timestamp") is not None:
            return by_timestamp.get(message["timestamp"])
        return by_sequence.get(message.get("_sequence"))

    incoming = [m for m in incoming if not m.get("streaming")]
    if not rewrite:
        result = [clean(m) for m in stored]
        seen = set(by_timestamp)
        for message in incoming:
            if index(message) is None and message.get("timestamp") not in seen:
                result.append(clean(message))
                if message.get("timestamp") is not None:
                    seen.add(message["timestamp"])
        return result

    def hydrate(message):
        position = index(message)
        if position is None:
            return clean(message)
        original = clean(stored[position])
        visible = client_message(original)
        updates = clean(client_message(message))
        # Omitted fields are common in older clients. Only explicit changes invalidate
        # signatures, whose replay contents must match the original message exactly.
        if all(visible.get(k) == v for k, v in updates.items()):
            return original
        return {**visible, **updates}

    if keep_prefix:
        positions = [index(m) for m in incoming if index(m) is not None]
        end = max(positions, default=-1)
        result = [clean(m) for m in stored[: end + 1]]
        for message in incoming:
            position = index(message)
            if position is None:
                result.append(hydrate(message))
            else:
                result[position] = hydrate(message)
        return result
    return [hydrate(m) for m in incoming]
