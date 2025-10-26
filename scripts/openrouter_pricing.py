#!/usr/bin/env python3

from asyncio import subprocess
import os
import re
import json
import asyncio
import aiohttp
from utils import download_urls

async def main():
    await download_urls({
        "openrouter_pricing_all.json": "https://openrouter.ai/api/frontend/models"
    })

    with open("../llms/llms.json") as f:
        llms = json.load(f)
        providers = llms.get("providers", {})
        provider_ids = list(providers.keys())
        print(f"Found {', '.join(provider_ids)} providers in llms.json")

        openrouter_pricing = {}
        openrouter = providers.get("openrouter", {})
        models = openrouter.get("models", {})
        print(f"Found {len(models)} models")

        with open("openrouter_pricing_all.json") as f:
            or_obj = json.load(f)
            or_models = or_obj.get("data", [])
            for model in models.keys():
                provider_model = models[model]
                for or_model in or_models:
                    if provider_model == or_model.get("slug"):
                        endpoint = or_model.get("endpoint", {})
                        if endpoint.get("pricing", {}) is not None:
                            pricing = endpoint.get("pricing", {})
                            openrouter_pricing[provider_model] = {
                                "input": pricing.get("prompt", 0),
                                "output": pricing.get("completion", 0)
                            }

        with open("openrouter_pricing.json", 'w') as f:
            json.dump(openrouter_pricing, f, indent=2)
            print(f"✓ Successfully created openrouter_pricing.json with {len(openrouter_pricing)} model pricings")

if __name__ == "__main__":
    asyncio.run(main())
