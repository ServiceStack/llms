import asyncio
import copy
import json
import re
from pathlib import Path

from .schema import ValidationError, require, text, validate_input, validate_recipe


async def generate(ctx, user, body, improve=False):
    text(body.get("goal"), "goal", 6000)
    text(body.get("model"), "model", 200)
    model = body["model"]
    require("jev" not in model.lower(), "model", "Select a text-generation model, not Jev.")
    for provider in ctx.get_providers().values():
        models = getattr(provider, "models", {}) or {}
        for candidate in models.values() if isinstance(models, dict) else models:
            if isinstance(candidate, dict) and model in (candidate.get("id"), candidate.get("name")):
                output = (candidate.get("modalities") or {}).get("output")
                require(not output or "text" in output, "model", "Select a model that produces text.")
    prompt = Path(__file__).with_name("prompts").joinpath("create-recipe.md").read_text()
    message = {"goal": body["goal"]}
    if improve:
        message["recipe"] = validate_recipe(body.get("recipe"))
    if "example" in body:
        message["example"] = body["example"]
    if body.get("repair"):
        text(body["repair"], "repair", 10000)
        message["invalidDraft"] = body["repair"]
    async with asyncio.timeout(90):
        response = await ctx.chat_completion(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(message, ensure_ascii=False)},
                ],
            },
            context={"tools": "none", "nohistory": True, "nostore": True, "user": user},
        )
    answer = (response.get("choices") or [{}])[0].get("message", {}).get("content", "")
    require(
        isinstance(answer, str) and answer.strip(),
        "generation",
        "The model returned no recipe. Try another text model.",
    )
    fence = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", answer, re.S)
    content = fence.group(1) if fence else answer
    try:
        parsed = json.loads(content)
        # Only actual runs can create new examples. Never accept examples invented
        # or modified by a text model, including echoes of existing recorded output.
        if isinstance(parsed, dict):
            # Generated drafts always use the current, separate metadata contract.
            parsed.setdefault("content", "")
            parsed["examples"] = []
        recipe = validate_recipe(parsed)
        if improve:
            original = message["recipe"]
            changed = any(
                original.get(key) != recipe.get(key) for key in ("inputSchema", "state", "questions", "decisionModel")
            )
            for saved in original.get("examples", []):
                try:
                    validate_input(recipe["inputSchema"], saved["input"])
                except ValidationError:
                    continue
                example = copy.deepcopy(saved)
                if changed:
                    example.pop("expected", None)
                    if example.pop("execution", None) is not None:
                        example.setdefault("notes", "Recipe changed; run this input again to record a new result.")
                recipe["examples"].append(example)
            recipe = validate_recipe(recipe)
        return {"recipe": recipe, "usage": response.get("usage"), "model": model}
    except (ValueError, RecursionError) as e:
        diagnostic = (
            str(e) if isinstance(e, ValidationError) else "The model did not return a single valid JSON recipe."
        )
        return {
            "recipe": None,
            "draft": content[:10000],
            "diagnostic": diagnostic,
            "usage": response.get("usage"),
            "model": model,
        }
