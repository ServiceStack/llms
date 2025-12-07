#!/usr/bin/env python3
"""
generate chutes_pricing.json from chutes models listed in openai_pricing_all.json
"""

import asyncio
import json

from utils import download_urls

async def main():
    chutes_pricing = {}
    chutes_pricing_all = {}

    with open("../llms/llms.json", encoding="utf-8") as f:
        llms = json.load(f)
        providers = llms.get("providers", {})
        provider_ids = list(providers.keys())
        print(f"Found {', '.join(provider_ids)} providers in llms.json")
        chutes = providers.get("chutes", {})
        llms_models = chutes.get("models", {})
        print(f"Found chutes {len(llms_models)} models")

    with open("openrouter_pricing_all.json", encoding="utf-8") as f:
        or_obj = json.load(f)
        or_models = or_obj.get("data", [])
        for or_model in or_models:
            # Check if the model has an endpoint name containing "Chutes"
            endpoint = or_model.get("endpoint", {})
            if endpoint is not None and "Chutes" == endpoint.get("provider_name", ""):
                if endpoint.get("pricing", {}) is not None:
                    pricing = endpoint.get("pricing", {})
                    model_name = or_model.get("slug")
                    input = pricing.get("prompt", 0)
                    if input != "0":
                        # use model_name to find model id llms_models
                        provider_model_id = endpoint.get("provider_model_id", "")
                        if provider_model_id in llms_models.values():
                            chutes_pricing[provider_model_id] = {
                                "input": pricing.get("prompt", 0),
                                "output": pricing.get("completion", 0),
                            }

                        chutes_pricing_all[model_name] = {
                            "input": pricing.get("prompt", 0),
                            "output": pricing.get("completion", 0),
                        }

        
        with open("chutes_pricing_all.json", "w", encoding="utf-8") as f:
            json.dump(chutes_pricing_all, f, indent=2)
            print(f"✓ Successfully created chutes_pricing_all.json with {len(chutes_pricing_all)} model pricings")

        with open("chutes_pricing.json", "w", encoding="utf-8") as f:
            json.dump(chutes_pricing, f, indent=2)
            print(f"✓ Successfully created chutes_pricing.json with {len(chutes_pricing)} model pricings")


if __name__ == "__main__":
    asyncio.run(main())
