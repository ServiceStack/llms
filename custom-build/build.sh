#!/bin/bash

# Create a custom build of llms.py with selected extensions

# extensions to include
EXTENSIONS="${EXTENSIONS:-providers}"

# Convert to array for easier manipulation and deduplication
IFS=',' read -r -a EXT_ARRAY <<< "$EXTENSIONS"

add_extension() {
    local new_ext="$1"
    for ext in "${EXT_ARRAY[@]}"; do
        if [[ "$ext" == "$new_ext" ]]; then
            return 0
        fi
    done
    EXT_ARRAY+=("$new_ext")
}

# enable UI (Look for existing UI variable or default to "1")
UI="${UI:-0}"

# If app,tools,analytics,gallery,katex,system_prompts are enabled, we should enable UI
for item in app tools analytics gallery katex system_prompts; do
    if [[ "${EXT_ARRAY[@]}" =~ "$item" ]]; then
        UI="1"
        break
    fi
done

# if UI == 1, we should add 'app' and 'tools' extensions
if [ "$UI" == "1" ]; then
    add_extension "app"
    add_extension "tools"
fi

rm -rf llms
mkdir -p llms/.llms/cache

cp ../llms/providers.json llms/providers.json
cp ../llms/llms.json llms/llms.json
cp ../llms/main.py llms/main.py
if [ -f ../.env ]; then
    echo "Copying .env"
    cp ../.env llms/.env; 
fi

if [ "$UI" == "1" ]; then
    echo "Copying /ui and index.html"
    cp ../llms/index.html llms/index.html; 
    cp -r ../llms/ui llms/ui;

    echo "Copying db.py"
    cp ../llms/db.py llms/db.py; 
fi

# copy extensions
mkdir -p llms/extensions
for ext in "${EXT_ARRAY[@]}"; do
    echo "Copying extension: $ext"
    cp -r ../llms/extensions/$ext llms/extensions/$ext
    done

# create llms.sh
cat <<EOF > llms/llms.sh
#!/bin/bash

LLMS_HOME=.llms PYTHONPATH=.. python3 -m llms "\$@"
EOF

# create __main__.py
cat <<EOF > llms/__main__.py
from .main import main

if __name__ == "__main__":
    main()
EOF

# create __init__.py
cat <<EOF > llms/__init__.py
from .main import main as main

__all__ = ["main"]
EOF

chmod +x llms/llms.sh
