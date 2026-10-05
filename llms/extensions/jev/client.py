"""OpenRouter's non-chat Decisions endpoint, with bounded responses and no hidden retries."""

import json

import aiohttp

from .schema import finite, require

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MAX_RESPONSE = 2 * 1024 * 1024


class DecisionError(Exception):
    def __init__(self, message, status=502, raw=None):
        self.status, self.raw = status, raw
        super().__init__(message)


def normalize_answers(response, questions):
    answers = response.get("answers") if isinstance(response, dict) else None
    require(
        isinstance(answers, dict) and set(answers) == set(questions),
        "response.answers",
        "The provider did not return every requested answer.",
    )
    normalized = {}
    for name, question in questions.items():
        answer = answers[name]
        path = "response.answers." + name
        kind = question["type"]
        require(isinstance(answer, dict) and answer.get("type") == kind, path, "Unexpected answer type.")
        result = {"type": kind}
        if kind == "noul":
            probability = answer.get("noul")
            require(finite(probability) and 0 <= probability <= 1, path, "Invalid probability of yes.")
            result["noul"] = probability
        else:
            options = (
                list(question["criteria"]) if kind == "choice" else [str(i) for i in range(len(question["criteria"]))]
            )
            probabilities = answer.get("probabilities")
            require(
                isinstance(probabilities, dict) and set(probabilities) == set(options),
                path,
                "Invalid probability options.",
            )
            require(all(finite(v) and 0 <= v <= 1 for v in probabilities.values()), path, "Invalid probabilities.")
            require(abs(sum(probabilities.values()) - 1) <= 0.025, path, "Probabilities do not sum to one.")
            confidence = answer.get("confidence")
            require(confidence is None or finite(confidence) and 0 <= confidence <= 1, path, "Invalid confidence.")
            result.update(probabilities={option: probabilities[option] for option in options}, confidence=confidence)
            if kind == "choice":
                require(answer.get("choice") in options, path, "The selected option is not in the question.")
                result["choice"] = answer["choice"]
            else:
                score = answer.get("score")
                require(finite(score) and 0 <= score <= len(options) - 1, path, "Invalid score.")
                legend = answer.get("legend")
                require(
                    legend is None or isinstance(legend, dict) and set(legend) == set(options),
                    path,
                    "Invalid scale legend.",
                )
                if legend is not None:
                    require(
                        all(legend[str(i)] == description for i, description in enumerate(question["criteria"])),
                        path,
                        "Provider scale differs from the submitted rubric.",
                    )
                result.update(score=score, legend={str(i): v for i, v in enumerate(question["criteria"])})
        normalized[name] = result
    return normalized


def provider_status(ctx):
    provider = ctx.get_registered_provider("openrouter")
    if not provider:
        return None, "Enable OpenRouter in provider settings to run Jev decisions."
    if not getattr(provider, "api_key", None):
        return None, "Add your OpenRouter API key in provider settings to run Jev decisions."
    return provider, "Connected to OpenRouter"


class DecisionClient:
    def __init__(self, ctx):
        self.ctx, self.session = ctx, None

    async def close(self):
        if self.session:
            await self.session.close()

    async def decide(self, payload):
        provider, message = provider_status(self.ctx)
        if not provider:
            raise DecisionError(message, 503)
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45, connect=10))
        headers = dict(getattr(provider, "headers", {}) or {})
        headers.update({"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"})
        try:
            async with self.session.post(ENDPOINT, headers=headers, json=payload, allow_redirects=False) as response:
                if response.status != 200:
                    messages = {
                        401: "OpenRouter rejected the API key. Check provider settings.",
                        402: "Your OpenRouter account needs credits.",
                        429: "OpenRouter is rate limiting requests. Wait a moment, then run again.",
                        400: "OpenRouter rejected this decision request. Review the questions and input.",
                        413: "This input is too large for OpenRouter. Try a shorter document.",
                    }
                    raise DecisionError(
                        messages.get(
                            response.status,
                            f"OpenRouter could not complete the request (HTTP {response.status}). Try again later.",
                        ),
                        response.status,
                    )
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise DecisionError("The provider response exceeded the size limit.")
                try:
                    raw = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    json.dumps(raw, allow_nan=False)
                except (ValueError, UnicodeDecodeError):
                    raise DecisionError("OpenRouter returned an unreadable response.") from None
                try:
                    answers = normalize_answers(raw, payload["questions"])
                except ValueError as e:
                    raise DecisionError(str(e), raw=raw) from None
                return raw, answers
        except TimeoutError:
            raise DecisionError(
                "The request timed out. It may have been processed by OpenRouter; use Run again for a new attempt.", 504
            ) from None
        except aiohttp.ClientError:
            raise DecisionError("Could not reach OpenRouter. Check your connection, then run again.", 502) from None
