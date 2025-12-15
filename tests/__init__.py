# Tests for llms-py package
import os
from os.path import abspath, dirname, join

from dotenv import load_dotenv

# Calculate project root path
project_root = dirname(dirname(abspath(__file__)))

# Load environment variables from .env file
dotenv_path = join(project_root, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
