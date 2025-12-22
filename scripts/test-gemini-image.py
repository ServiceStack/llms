#!/usr/bin/env python

import base64
import datetime
import json
import os

import requests

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is not set")


model = "gemini-2.5-flash-image"
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
headers = {"x-goog-api-key": API_KEY, "Content-Type": "application/json"}

payload = {
    "contents": [
        {
            "role": "user",
            "parts": [
                {
                    "text": "A realistic depiction of a grand library during a foggy autumn dawn. Muted sunlight struggles to penetrate the thick fog outside."
                }
            ],
        }
    ],
    # only pro
    # "tools": [{"google_search": {}}],
    "generationConfig": {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": "9:16"},
    },
}

print(f"Requesting {url}...")
response = requests.post(url, headers=headers, json=payload)

if response.status_code != 200:
    print(f"Error: {response.status_code} - {response.text}")
    exit(1)

result = response.json()

# Create dist directory if it doesn't exist
os.makedirs("./dist", exist_ok=True)

date_fmt = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
base_filename = f"{model}-{date_fmt}"
json_filename = f"./dist/{base_filename}.json"

with open(json_filename, "w") as f:
    f.write(json.dumps(result, indent=2))
print(f"Saved response to {json_filename}")

# Try to extract and save images
try:
    if "candidates" in result:
        for i, candidate in enumerate(result["candidates"]):
            if "content" in candidate and "parts" in candidate["content"]:
                for j, part in enumerate(candidate["content"]["parts"]):
                    if "inlineData" in part:
                        mime_type = part["inlineData"].get("mimeType", "image/png")
                        data = part["inlineData"]["data"]

                        ext = "png"
                        if "jpeg" in mime_type:
                            ext = "jpg"
                        elif "webp" in mime_type:
                            ext = "webp"

                        image_filename = f"./dist/{base_filename}-{i}-{j}.{ext}"

                        with open(image_filename, "wb") as f:
                            f.write(base64.b64decode(data))

                        print(f"Saved image to {image_filename}")
                    elif "text" in part:
                        print(f"Text response: {part['text']}")
    else:
        print("No candidates found in response.")

except Exception as e:
    print(f"Error processing response: {e}")
