#!/bin/bash

# get all provider ids
# provider_ids=$(jq -r '.providers | keys[]' ../llms/llms.json)
#echo "provider_ids: $provider_ids"

curl https://models.dev/api.json > models-dev.json

provider_keys=$(for k in $(jq -r '.providers | keys[]' ../llms/llms.json); do
  printf '"%s": .["%s"],' "$k" "$k"
done | sed 's/,$//')
jq -rc "{ $provider_keys }" models-dev.json > ../llms/providers.json
jq -r "{ $provider_keys }" models-dev.json > models.json

rm models-dev.json