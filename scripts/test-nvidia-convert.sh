#!/bin/bash

result="$1"

# example json
# { "artifacts": [{ "base64": "...", "finishReason":"SUCCESS", "seed": 1 }] }

# for each artifact from json in $result, download it
cat "$result" | jq -c '.artifacts[]' | while read -r artifact; do
     # convert base64 to file
    seed=$(echo "$artifact" | jq -r '.seed')
    filename="nvidia-${seed}.png"
    echo "Saving $filename..."
    echo "$artifact" | jq -r '.base64' | base64 -d > "$filename"
done
