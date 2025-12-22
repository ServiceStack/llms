#!/bin/bash

cp ./llms/providers-extra.json ~/.llms/providers-extra.json
cp ./llms/providers.json ~/.llms/providers.json
cp ./llms/llms.json ~/.llms/llms.json

python -m llms $@
