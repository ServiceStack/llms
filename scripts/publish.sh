#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly LLMS_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly USERS_DIR="$(cd -- "$LLMS_ROOT/llms-home/user" && pwd -P)"
readonly CURRENT_DIR="$(pwd -P)"

fail() {
    printf 'llms-publish: %s\n' "$*" >&2
    exit 1
}

command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"

case "$CURRENT_DIR/" in
    "$USERS_DIR"/*/projects/*/) ;;
    *) fail "run this from a project or its dist folder under $USERS_DIR/<user>/projects" ;;
esac

relative_path="${CURRENT_DIR#"$USERS_DIR"/}"
IFS=/ read -r user projects_segment project_folder extra_segment remainder <<< "$relative_path"

[[ "$projects_segment" == "projects" && -n "$user" && -n "$project_folder" ]] || \
    fail "could not determine the user and project from $CURRENT_DIR"
[[ -z "${extra_segment:-}" || ( "$extra_segment" == "dist" && -z "${remainder:-}" ) ]] || \
    fail "run this from the project root or its dist folder"

readonly USER_DIR="$USERS_DIR/$user"
readonly PROJECT_DIR="$USER_DIR/projects/$project_folder"
readonly PROJECTS_FILE="$USER_DIR/projects/projects.json"
readonly CONFIG_FILE="$USER_DIR/publish/config.json"

[[ -f "$CONFIG_FILE" ]] || fail "publish credentials not found: $CONFIG_FILE"
[[ -f "$PROJECTS_FILE" ]] || fail "projects file not found: $PROJECTS_FILE"

api_key="$(jq -er '.apiKey | select(type == "string" and length > 0)' "$CONFIG_FILE")" || \
    fail "apiKey is missing from $CONFIG_FILE"
base_url="$(jq -r '.baseUrl // "https://ai.llmspy.org"' "$CONFIG_FILE")"

project_json="$(jq -ce --arg folder "$project_folder" '
    map(select((.folder // ((.name // "")
        | gsub("[^A-Za-z0-9_ -]"; "")
        | gsub("[ _]+"; "-")
        | gsub("-+"; "-")
        | ascii_downcase)) == $folder))
    | if length == 0 then error("project not found") else .[0] end
' "$PROJECTS_FILE" 2>/dev/null)" || fail "project '$project_folder' not found in $PROJECTS_FILE"

project_name="$(jq -er '.name | select(type == "string" and length > 0)' <<< "$project_json")" || \
    fail "project '$project_folder' has no name"
publish_path="$(jq -er 'if has("publish") then (.publish // error("null publish path")) else error("missing publish path") end' \
    <<< "$project_json" 2>/dev/null)" || fail "project '$project_name' has no publish directory configured"

[[ "$publish_path" != /* ]] || fail "publish directory must be within the project folder"
publish_dir="$(realpath -e -- "$PROJECT_DIR/${publish_path:-.}" 2>/dev/null)" || \
    fail "publish directory does not exist: ${publish_path:-project root}"
[[ -d "$publish_dir" ]] || fail "publish path is not a directory: $publish_dir"
case "$publish_dir/" in
    "$PROJECT_DIR"/) ;;
    "$PROJECT_DIR"/*/) ;;
    *) fail "publish directory must be within the project folder" ;;
esac

tmp_dir="$(mktemp -d)"
cleanup() { rm -rf -- "$tmp_dir"; }
trap cleanup EXIT

info_file="$tmp_dir/info.json"
archive_file="$tmp_dir/$project_folder.tar.gz"
response_file="$tmp_dir/response.json"
printf '%s\n' "$project_json" > "$info_file"
tar -C "$publish_dir" -czf "$archive_file" .

encoded_name="$(jq -rn --arg value "$project_name" '$value | @uri')"
publish_url="${base_url%/}/publish/project/$encoded_name"
curl_args=(--silent --show-error --output "$response_file" --write-out '%{http_code}'
    --header "Authorization: Bearer $api_key"
    --header 'Accept: application/json'
    --form "info=@$info_file;type=application/json;filename=info.json"
    --form "file=@$archive_file;type=application/gzip;filename=$project_name.tar.gz")
[[ "$publish_url" == https://localhost:5001/* ]] && curl_args+=(--insecure)

printf "Publishing '%s' from %s\n" "$project_name" "$publish_dir"
status="$(curl "${curl_args[@]}" "$publish_url")" || fail "upload failed"

if [[ "$status" != 2* ]]; then
    printf 'llms-publish: server returned HTTP %s\n' "$status" >&2
    cat "$response_file" >&2
    printf '\n' >&2
    exit 1
fi

published_url="$(jq -er '.publishedUrl | select(type == "string" and length > 0)' "$response_file" 2>/dev/null)" || {
    cat "$response_file"
    fail "publish succeeded but the response contained no publishedUrl"
}

updated_projects="$tmp_dir/projects.json"
jq --arg folder "$project_folder" --arg url "$published_url" '
    map(if ((.folder // ((.name // "")
        | gsub("[^A-Za-z0-9_ -]"; "")
        | gsub("[ _]+"; "-")
        | gsub("-+"; "-")
        | ascii_downcase)) == $folder)
        then .publishedUrl = $url else . end)
' "$PROJECTS_FILE" > "$updated_projects"
chmod --reference="$PROJECTS_FILE" "$updated_projects"
mv -- "$updated_projects" "$PROJECTS_FILE"

printf 'Published: %s\n' "$published_url"
