"""Portable, allowlisted recorded outputs for public shares and recipe examples."""

import copy
import json
from datetime import datetime

from .client import normalize_answers
from .schema import compile_request, finite, require, text
from .storage import document_hash

MAX_EXECUTION_BYTES = 2 * 1024 * 1024


def bounded_json(value, path="publication", depth=0):
    require(depth <= 32, path, "Use at most 32 JSON nesting levels.")
    if isinstance(value, dict):
        for child in value.values():
            bounded_json(child, path, depth + 1)
    elif isinstance(value, list):
        for child in value:
            bounded_json(child, path, depth + 1)


def validate_execution(document, execution):
    bounded_json(execution, "execution")
    require(isinstance(execution, dict), "execution", "A successful recorded execution is required.")
    require(
        not set(execution) - {"status", "input", "prompt", "answers", "model", "completedAt", "durationMs"},
        "execution",
        "Remove private or unsupported execution fields.",
    )
    require(execution.get("status") == "succeeded", "execution.status", "Run this recipe successfully first.")
    request = compile_request(document, execution.get("input"))
    require(
        document_hash(execution.get("prompt")) == document_hash(request["state"]),
        "execution.prompt",
        "Prompt must match the recorded input.",
    )
    normalized = normalize_answers({"answers": execution.get("answers")}, document["questions"])
    require(
        normalized == execution["answers"],
        "execution.answers",
        "Use complete normalized results without private fields.",
    )
    text(execution.get("model"), "execution.model", 200)
    try:
        completed = datetime.fromisoformat(execution.get("completedAt", "").replace("Z", "+00:00"))
        require(
            completed.utcoffset() is not None and completed.utcoffset().total_seconds() == 0,
            "execution.completedAt",
            "Use a UTC completion time.",
        )
    except (ValueError, TypeError, AttributeError):
        require(False, "execution.completedAt", "Use a UTC completion time.")
    if "durationMs" in execution:
        require(
            finite(execution["durationMs"]) and execution["durationMs"] >= 0,
            "execution.durationMs",
            "Use a nonnegative duration.",
        )
    require(
        len(json.dumps(execution, ensure_ascii=False, allow_nan=False).encode()) <= MAX_EXECUTION_BYTES,
        "execution",
        "Run a smaller example: recorded results must fit within 2 MiB.",
    )
    return copy.deepcopy(execution)
