#!/usr/bin/env python3
"""
Convert OpenAI pricing markdown to JSON format.
Parses markdown tables and organizes pricing by category and tier.

Source: https://platform.openai.com/docs/pricing [Copy page] -> openai_pricing.md
"""

import json
import re
from pathlib import Path
import subprocess
from typing import Dict, List, Any


def parse_markdown_table(lines: List[str], start_idx: int) -> tuple[List[Dict[str, str]], int]:
    """
    Parse a markdown table starting at start_idx.
    Returns (list of row dicts, index of next line after table).
    """
    # Skip header separator line
    if start_idx + 1 >= len(lines) or not lines[start_idx + 1].startswith('|---'):
        return [], start_idx
    
    header_line = lines[start_idx]
    headers = [h.strip() for h in header_line.split('|')[1:-1]]
    
    rows = []
    idx = start_idx + 2
    
    while idx < len(lines):
        line = lines[idx].strip()
        if not line.startswith('|') or line.startswith('|---'):
            break
        
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if len(cells) == len(headers):
            row = {headers[i]: cells[i] for i in range(len(headers))}
            rows.append(row)
        
        idx += 1
    
    return rows, idx


def convert_price_to_float(price_str: str) -> float | None:
    """Convert price string like '$1.25' or '-' to float."""
    price_str = price_str.strip()
    if price_str == '-' or price_str == '':
        return None
    
    # Remove $ and any other non-numeric characters except decimal point
    price_str = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(price_str)
    except ValueError:
        return None


def parse_pricing_markdown(file_path: str) -> Dict[str, Any]:
    """Parse the OpenAI pricing markdown file."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    pricing_data = {
        "text_tokens": {},
        "image_tokens": {},
        "audio_tokens": {},
        "video": [],
        "fine_tuning": {},
        "built_in_tools": [],
        "transcription_and_speech": {},
        "image_generation": {},
        "embeddings": [],
        "legacy_models": {}
    }
    
    i = 0
    current_section = None
    current_subsection = None
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Detect main sections
        if line.lower() == "text tokens":
            current_section = "text_tokens"
            i += 1
            continue
        elif line.lower() == "image tokens":
            current_section = "image_tokens"
            i += 1
            continue
        elif line.lower() == "audio tokens":
            current_section = "audio_tokens"
            i += 1
            continue
        elif line.lower() == "video":
            current_section = "video"
            i += 1
            continue
        elif line.lower() == "fine-tuning":
            current_section = "fine_tuning"
            i += 1
            continue
        elif line.lower() == "built-in tools":
            current_section = "built_in_tools"
            i += 1
            continue
        elif line.lower() == "transcription and speech generation":
            current_section = "transcription_and_speech"
            i += 1
            continue
        elif line.lower() == "image generation":
            current_section = "image_generation"
            i += 1
            continue
        elif line.lower() == "embeddings":
            current_section = "embeddings"
            i += 1
            continue
        elif line.lower() == "legacy models":
            current_section = "legacy_models"
            i += 1
            continue
        
        # Detect subsections (pricing tiers)
        if line.lower() in ["batch", "flex", "standard", "priority"]:
            current_subsection = line.lower()
            i += 1
            continue
        
        # Parse tables
        if line.startswith('|') and not line.startswith('|---'):
            rows, next_idx = parse_markdown_table(lines, i)
            
            if current_section == "text_tokens":
                if current_subsection:
                    if current_subsection not in pricing_data["text_tokens"]:
                        pricing_data["text_tokens"][current_subsection] = []
                    pricing_data["text_tokens"][current_subsection] = rows
            
            elif current_section == "image_tokens":
                pricing_data["image_tokens"] = rows
            
            elif current_section == "audio_tokens":
                pricing_data["audio_tokens"] = rows
            
            elif current_section == "video":
                pricing_data["video"] = rows
            
            elif current_section == "fine_tuning":
                if current_subsection:
                    if current_subsection not in pricing_data["fine_tuning"]:
                        pricing_data["fine_tuning"][current_subsection] = []
                    pricing_data["fine_tuning"][current_subsection] = rows
            
            elif current_section == "built_in_tools":
                pricing_data["built_in_tools"] = rows
            
            elif current_section == "transcription_and_speech":
                if current_subsection:
                    if current_subsection not in pricing_data["transcription_and_speech"]:
                        pricing_data["transcription_and_speech"][current_subsection] = []
                    pricing_data["transcription_and_speech"][current_subsection] = rows
            
            elif current_section == "image_generation":
                pricing_data["image_generation"] = rows
            
            elif current_section == "embeddings":
                pricing_data["embeddings"] = rows
            
            elif current_section == "legacy_models":
                if current_subsection:
                    if current_subsection not in pricing_data["legacy_models"]:
                        pricing_data["legacy_models"][current_subsection] = []
                    pricing_data["legacy_models"][current_subsection] = rows
            
            i = next_idx
            continue
        
        i += 1
    
    return pricing_data


def price_to_per_token_string(price_str: str) -> str | None:
    """Convert price string like '$1.25' to per-token decimal string."""
    price = convert_price_to_float(price_str)
    if price is None:
        return None
    per_token = price / 1_000_000
    # Format as string with up to 20 decimal places, removing trailing zeros
    return format(per_token, '.20f').rstrip('0').rstrip('.')


def extract_standard_pricing(pricing_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract all models with their Standard pricing into a flat list.
    Converts prices to decimal format (cost per token) as strings.
    """
    models = []

    # Extract from text_tokens standard pricing
    if "text_tokens" in pricing_data and "standard" in pricing_data["text_tokens"]:
        for model in pricing_data["text_tokens"]["standard"]:
            model_entry = {
                "Model": model.get("Model"),
                "Input": price_to_per_token_string(model.get("Input", "-")),
                "Cached input": price_to_per_token_string(model.get("Cached input", "-")),
                "Output": price_to_per_token_string(model.get("Output", "-"))
            }
            models.append(model_entry)

    # Extract from fine_tuning standard pricing
    if "fine_tuning" in pricing_data and "standard" in pricing_data["fine_tuning"]:
        for model in pricing_data["fine_tuning"]["standard"]:
            model_entry = {
                "Model": model.get("Model"),
                "Training": model.get("Training"),
                "Input": price_to_per_token_string(model.get("Input", "-")),
                "Cached Input": price_to_per_token_string(model.get("Cached Input", "-")),
                "Output": price_to_per_token_string(model.get("Output", "-"))
            }
            models.append(model_entry)

    # Extract from legacy_models standard pricing
    if "legacy_models" in pricing_data and "standard" in pricing_data["legacy_models"]:
        for model in pricing_data["legacy_models"]["standard"]:
            model_entry = {
                "Model": model.get("Model"),
                "Input": price_to_per_token_string(model.get("Input", "-")),
                "Output": price_to_per_token_string(model.get("Output", "-"))
            }
            models.append(model_entry)

    # Extract from transcription_and_speech standard pricing
    if "transcription_and_speech" in pricing_data and "standard" in pricing_data["transcription_and_speech"]:
        for model in pricing_data["transcription_and_speech"]["standard"]:
            model_entry = {
                "Model": model.get("Model"),
                "Use case": model.get("Use case"),
                "Cost": model.get("Cost")
            }
            models.append(model_entry)

    return models


def main():
    """Main entry point."""

    script_dir = Path(__file__).parent
    md_file = script_dir / "openai_pricing.md"
    json_file = script_dir / "openai_pricing_all.json"
    json_pricing_file = script_dir / "openai_pricing.json"

    if not md_file.exists():
        print(f"Error: {md_file} not found")
        return

    print(f"Parsing {md_file}...")
    pricing_data = parse_pricing_markdown(str(md_file))

    print(f"Writing to {json_file}...")
    with open(json_file, 'w') as f:
        json.dump(pricing_data, f, indent=2)

    print(f"✓ Successfully converted to {json_file}")
    print(f"  Total sections: {len(pricing_data)}")

    # Extract and save standard pricing
    print(f"Extracting standard pricing...")
    openai_pricing = extract_standard_pricing(pricing_data)

    with open("../llms/llms.json") as f:
        llms = json.load(f)
        providers = llms.get("providers", {})
        provider = providers.get("openai", {})
        models = provider.get("models", {})

        billing = {}
        for model in models.keys():
            provider_model = models[model]
            model_info = next((m for m in openai_pricing if m.get("Model") == provider_model), None)
            if model_info:
                billing[provider_model] = {
                    "input": model_info.get("Input"),
                    "output": model_info.get("Output")
                }
        with open(json_pricing_file, 'w') as f:
            json.dump(billing, f, indent=2)
            print(f"✓ Successfully created {json_pricing_file}")
            print(f"  Total models: {len(billing)}")

if __name__ == "__main__":
    main()
