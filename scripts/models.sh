#!/bin/bash

# get all provider ids
# provider_ids=$(jq -r '.providers | keys[]' ../llms/llms.json)
#echo "provider_ids: $provider_ids"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR="$SCRIPT_DIR/.."
LLMS_JSON="$ROOT_DIR/llms/llms.json"
PROVIDERS_OUT="$ROOT_DIR/llms/providers.json"
MODELS_OUT="$SCRIPT_DIR/models.json"

curl https://models.dev/api.json > models-dev.json

provider_keys=$(for k in $(jq -r '.providers | keys[]' "$LLMS_JSON"); do
  printf '"%s": .["%s"],' "$k" "$k"
done | sed 's/,$//')
jq -rc "{ $provider_keys }" models-dev.json > "$PROVIDERS_OUT"
jq -r "{ $provider_keys }" models-dev.json > "$MODELS_OUT"

rm models-dev.json