import sys
import os
from pathlib import Path

# Add the repo root to sys.path
repo_root = Path(__file__).resolve().parent
sys.path.append(str(repo_root))

import importlib

main_module = importlib.import_module("llms.main")


# Mock g_app since it's used in ExtensionContext
class MockApp:
    def __init__(self):
        self.all_providers = []
        self.aspect_ratios = {}
        self.server_add_get = []
        self.server_add_post = []
        self.ui_extensions = []


main_module.g_app = MockApp()
main_module._ROOT = repo_root / "llms"  # Set _ROOT manually as it's set in main() normally

print(f"ROOT: {main_module._ROOT}")

try:
    main_module.load_builtin_extensions()

    found_chutes = False
    for provider in main_module.g_app.all_providers:
        if provider.__name__ == "ChutesImage":
            found_chutes = True
            print("SUCCESS: ChutesImage provider found!")
            break

    if not found_chutes:
        print("FAILURE: ChutesImage provider NOT found.")
        print(f"Providers found: {[p.__name__ for p in main_module.g_app.all_providers]}")
        sys.exit(1)

except Exception as e:
    print(f"An error occurred: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
