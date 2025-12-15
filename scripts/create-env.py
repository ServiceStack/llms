#!/usr/bin/env python3
"""
Generate ../.env based from all the provider.env[] in providers.json
API_KEYS are needed in .env to run integration tests
"""

import json
import os


def main():
    with open("../llms/providers.json", encoding="utf-8") as f:
        providers = json.load(f)

    include = []
    for provider in providers.values():
        if provider and "env" in provider:
            for env_var in provider["env"]:
                if os.getenv(env_var) and env_var not in include:
                    include.append(env_var)

    include.sort()
    with open("../.env", "w", encoding="utf-8") as f:
        for env_var in include:
            f.write(f"{env_var}={os.getenv(env_var)}\n")


if __name__ == "__main__":
    main()
