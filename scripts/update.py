#!/usr/bin/env python3

import subprocess
import os
import re
import json
import asyncio
import aiohttp

from utils import price_to_string

async def main():
    # Run ./openai_pricing.py to generate openai_pricing.json
    out = subprocess.run(["python", "./openai_pricing.py"], check=True)
    print(out.stdout)

    # Run ./openrouter_pricing.py to generate openrouter_pricing.json
    out = subprocess.run(["python", "./openrouter_pricing.py"], check=True)
    print(out.stdout)

    with open("../llms/llms.json") as f:
        llms = json.load(f)
        providers = llms.get("providers", {})
        provider_ids = list(providers.keys())
        print(f"Found {', '.join(provider_ids)} providers in llms.json")

        free_providers = ["openrouter_free", "groq", "google_free", "codestral", "ollama"]
        for provider_id in provider_ids:
            print(f"Processing {provider_id}")
            provider = providers[provider_id]
            models = provider.get("models", {})
            print(f"Found {len(models)} models for {provider_id}")
            pricing = {}
            
            if provider_id in free_providers:
                print(f"Setting {provider_id} to free")
                provider["default_pricing"] = {
                  "input": "0",
                  "output": "0"
                }
            elif os.path.exists(f"{provider_id}_pricing.json"):
                with open(f"{provider_id}_pricing.json") as f:
                    pricing = json.load(f)
                    provider["pricing"] = pricing

            # Convert all provider pricing prices to strings
            for model_name, prices in provider.get("pricing", {}).items():
                print(f"Processing {model_name} for {provider_id} = {prices}")
                input_price = prices.get("input")
                output_price = prices.get("output")
                prices["input"] = price_to_string(input_price)
                prices["output"] = price_to_string(output_price)

        with open("../llms/llms.json", "w") as f:
            print("Saving llms.json")
            f.write(json.dumps(llms, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
