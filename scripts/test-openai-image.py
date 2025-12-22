#!/usr/bin/env python

import asyncio
import base64
import datetime
import json
import mimetypes
import os

import aiohttp

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is not set")

url = "https://api.openai.com/v1/images/generations"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}


def aspect_ratio_to_size(aspect_ratio, model):
    width, height = aspect_ratio.split(":")
    if model == "dall-e-2":
        return "1024x1024"
    if model == "dall-e-3":
        if width > height:
            return "1792x1024"
        elif height > width:
            return "1024x1792"
    if width > height:
        return "1536x1024"
    elif height > width:
        return "1024x1536"
    return "1024x1024"


# dall-e-2, dall-e-3, or a GPT image model:
# gpt-image-1, gpt-image-1-mini, gpt-image-1.5
model = "dall-e-3"
payload = {
    "model": model,
    "prompt": "A realistic depiction of a grand library during a foggy autumn dawn. Muted sunlight struggles to penetrate the thick fog outside.",
    "size": aspect_ratio_to_size("9:16", model),
}


async def main():
    print(f"Requesting {url}...")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status != 200:
                text = await response.text()
                print(f"Error: {response.status} - {text}")
                exit(1)

            result = await response.json()

        # Create dist directory if it doesn't exist
        os.makedirs("./dist", exist_ok=True)

        date_fmt = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Clean model name for filename
        model_name = payload["model"].replace("/", "_")
        base_filename = f"{model_name}-{date_fmt}"
        json_filename = f"./dist/{base_filename}.json"

        with open(json_filename, "w") as f:
            f.write(json.dumps(result, indent=2))
        print(f"Saved response to {json_filename}")

        # Try to extract and save images
        try:
            if "data" in result:
                for i, item in enumerate(result["data"]):
                    image_url = item.get("url")
                    b64_json = item.get("b64_json")

                    ext = "png"
                    image_data = None

                    if b64_json:
                        image_data = base64.b64decode(b64_json)
                    elif image_url:
                        print(f"Downloading image from {image_url}...")
                        async with aiohttp.ClientSession() as session, session.get(image_url) as res:
                            if res.status == 200:
                                image_data = await res.read()
                                content_type = res.headers.get("Content-Type")
                                if content_type:
                                    ext = mimetypes.guess_extension(content_type)
                                    if ext:
                                        ext = ext.lstrip(".")  # remove leading dot
                                # Fallback if guess_extension returns None or if we want to be safe
                                if not ext:
                                    ext = "png"
                            else:
                                print(f"Failed to download image: {res.status}")

                    if image_data:
                        image_filename = f"./dist/{base_filename}-{i}.{ext}"
                        with open(image_filename, "wb") as f:
                            f.write(image_data)
                        print(f"Saved image to {image_filename}")
                    else:
                        print(f"No image data found for item {i}")
            else:
                print("No 'data' field in response.")

        except Exception as e:
            print(f"Error processing response: {e}")


if __name__ == "__main__":
    asyncio.run(main())
