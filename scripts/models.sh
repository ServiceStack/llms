#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cp "$DIR/../llms/providers-extra.json" ~/.llms/providers-extra.json
"$DIR/../llms.sh" --update-providers
cp ~/.llms/providers.json -f "$DIR/../llms/providers.json"
echo "Providers updated: $DIR/../llms/providers.json"
