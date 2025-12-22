#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# https://chutes-hunyuan-image-3.chutes.ai/generate
# https://chutes-qwen-image-edit-2509.chutes.ai/generate
# https://chutes-z-image-turbo.chutes.ai/generate

# model="stabilityai/stable-diffusion-xl-base-1.0"
# model="chroma"
# model="JuggernautXL"
# model="JuggernautXL-Ragnarok"
# model="HassakuXL"
# model="iLustMix"
# model="NovaFurryXL"
# model="Lykon/dreamshaper-xl-1-0"
# model="Illustrij"
# model="Animij"
# model="diagonalge/Booba"
model="FLUX.1-schnell"

prompt="A realistic depiction of a grand library during a foggy autumn dawn. Muted sunlight struggles to penetrate the thick fog outside."

mkdir -p "${DIR}/dist"

curl -X POST \
    https://image.chutes.ai/generate \
    -H "Authorization: Bearer $CHUTES_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF > "${DIR}/dist/chutes-$(date +%s).png"
{
    "model": "$model",
    "prompt": "$prompt",
    "negative_prompt": "blur, distortion, low quality",
    "guidance_scale": 7.5,
    "width": 1024,
    "height": 1024,
    "num_inference_steps": 50
}
EOF
