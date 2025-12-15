#!/usr/bin/env python3
"""
generate models.json with only the providers with models listed in llms.json
"""

import asyncio
import json


async def main():
    with open("../llms/llms.json", encoding="utf-8") as f:
        llms = json.load(f)
        providers = llms.get("providers", {})
        provider_ids = list(providers.keys())
        print(f"Found {', '.join(provider_ids)} providers in llms.json")

    # load ./models-dev.json
    models_file = "models-dev.json"
    with open(models_file, encoding="utf-8") as f:
        models_dev = json.load(f)
        all_models = {}
        for p in models_dev.values():
            if isinstance(p, dict) and "models" in p:
                all_models.update(p["models"])

        print(f"Found {len(all_models)} models in {models_file}")
        providers = {}
        for provider_id in provider_ids:
            print(f"Processing {provider_id}")
            provider = llms["providers"][provider_id]
            # Use model_map if exists, otherwise load all provider models
            if "model_map" in provider:
                model_map = provider.get("model_map", {})
                print(f"Found {len(model_map)} mapped models for {provider_id}")
                provider_models = {}
                for model_alias, model_id in model_map.items():
                    if model_id in all_models:
                        provider_models[model_alias] = all_models[model_id]
                    else:
                        print(f"Model {model_id} not found in {models_file}")
            else:
                # If no model_map, try to find provider in models_dev and use all its models
                source_id = provider_id
                if source_id in models_dev and "models" in models_dev[source_id]:
                    models = models_dev[source_id]["models"]
                    provider_models = models
                    print(f"Found {len(provider_models)} models for {provider_id} (no map)")
                else:
                    print(f"No models found for {provider_id} in {models_file}")
                    provider_models = {}

            providers[provider_id] = {
                "enabled": provider.get("enabled", True),
                "env": provider.get("env", []),
                "npm": provider.get("npm", ""),
                "api": provider.get("api", ""),
                "name": provider.get("name", ""),
                "doc": provider.get("doc", ""),
                "models": provider_models,
            }

    with open("../llms/providers.json", "w", encoding="utf-8") as f:
        json.dump(providers, f)
    with open("models.json", "w", encoding="utf-8") as f:
        json.dump(providers, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
