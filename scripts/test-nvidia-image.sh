#!/bin/bash

invoke_url='https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev'

authorization_header="Authorization: Bearer $NVIDIA_API_KEY"
accept_header='Accept: application/json'
content_type_header='Content-Type: application/json'

prompt="$1"

# if prompt empty use "Cat"
if [ -z "$prompt" ]; then
    prompt="Create an image of a futuristic city"
fi

# use cat EOF

data=$(cat <<EOF
{
  "prompt": "${prompt}",
  "mode": "base",
  "cfg_scale": 3.5,
  "width": 1024,
  "height": 1024,
  "seed": 0,
  "steps": 50
}
EOF
)

response=$(curl --silent -i -w "\n%{http_code}" --request POST \
  --url "$invoke_url" \
  --header "$authorization_header" \
  --header "$accept_header" \
  --header "$content_type_header" \
  --data "$data"
)

http_code=$(echo "$response" | tail -n 1)


if [ "$http_code" -eq 200 ]; then
    result="./dist/nvidia-image-$(date +%Y-%m-%d_%H-%M-%S).json"
    echo "Success: $result"
    echo "$response" | awk '/{/,EOF-1' | jq > "$result"

    # example json
    # { "artifacts": [{ "base64": "...", "finishReason":"SUCCESS", "seed": 1 }] }

    # for each artifact from json in $result, download it
    cat "$result" | jq -c '.artifacts[]' | while read -r artifact; do
        # convert base64 to file
        seed=$(echo "$artifact" | jq -r '.seed')
        filename="./dist/nvidia-${seed}.png"
        echo "Saving $filename..."
        echo "$artifact" | jq -r '.base64' | base64 -d > "$filename"
    done
else
    echo "Failed:"
    echo "$response" | awk '/{/,EOF-1' | jq
fi
