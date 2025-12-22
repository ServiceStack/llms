#!/usr/bin/env python

import base64
import datetime
import json
import os

import requests

API_KEY = os.getenv("OPENROUTER_API_KEY")
url = "https://openrouter.ai/api/v1/chat/completions"
headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# model = "google/gemini-2.5-flash-image-preview"
model = "openai/gpt-5-image"

payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": "A realistic depiction of a grand library during a foggy autumn dawn. Muted sunlight struggles to penetrate the thick fog outside.",
        }
    ],
    "modalities": ["image", "text"],
    "image_config": {"aspect_ratio": "9:16"},
}

response = requests.post(url, headers=headers, json=payload)
result = response.json()

if result.get("choices"):
    message = result["choices"][0]["message"]
    if message.get("images"):
        for image in message["images"]:
            data_uri = image["image_url"]["url"]
            base64_data = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
            date_fmt = f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            model_name = model.split("/")[-1].replace("2.5-", "").replace("-preview", "")
            filename = f"./dist/{model_name}-{date_fmt}.png"
            with open(f"./dist/{model_name}-{date_fmt}.json", "w") as f:
                f.write(json.dumps(result, indent=2))
            with open(filename, "wb") as f:
                f.write(base64.b64decode(base64_data))

            print(f"Generated image: {filename}")
