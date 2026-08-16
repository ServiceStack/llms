#!/usr/bin/env bash
#
# test-docker.sh — smoke test the published llms-py Docker image.
#
# Pulls the latest image (unless --no-pull) and exercises it end to end:
# image metadata, multi-arch manifest, toolchain, CLI, config init, and a
# live HTTP server with health checks.
#
# Usage:
#   ./scripts/test-docker.sh                      # test ghcr.io/servicestack/llms:latest
#   ./scripts/test-docker.sh --image llms-py:dev  # test a locally built image
#   ./scripts/test-docker.sh --no-pull            # skip docker pull
#   ./scripts/test-docker.sh --quick              # skip the server tests
#   ./scripts/test-docker.sh --keep               # don't remove the test container
#
# Exit code is 0 only if every test passed.

set -uo pipefail

IMAGE="${LLMS_DOCKER_IMAGE:-ghcr.io/servicestack/llms:latest}"
PORT=""
DO_PULL=1
DO_SERVER=1
DO_KEEP=0
CONTAINER="llms-test-$$"
WORKDIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --image)    IMAGE="$2"; shift 2 ;;
        --image=*)  IMAGE="${1#*=}"; shift ;;
        --port)     PORT="$2"; shift 2 ;;
        --port=*)   PORT="${1#*=}"; shift ;;
        --no-pull)  DO_PULL=0; shift ;;
        --quick)    DO_SERVER=0; shift ;;
        --keep)     DO_KEEP=1; shift ;;
        -h|--help)  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)          echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------- output ----

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
    YEL=$'\033[33m'; CYA=$'\033[36m'; N=$'\033[0m'
else
    B=""; DIM=""; RED=""; GRN=""; YEL=""; CYA=""; N=""
fi

PASSED=0; FAILED=0; SKIPPED=0
FAILURES=()

section() { printf '\n%s%s%s\n' "$B$CYA" "$1" "$N"; }
pass()    { PASSED=$((PASSED+1)); printf '  %s✓%s %s\n' "$GRN" "$N" "$1"; }
fail()    { FAILED=$((FAILED+1)); FAILURES+=("$1"); printf '  %s✗%s %s\n' "$RED" "$N" "$1"
            [ $# -gt 1 ] && printf '    %s%s%s\n' "$DIM" "$2" "$N"; return 0; }
skip()    { SKIPPED=$((SKIPPED+1)); printf '  %s–%s %s %s(%s)%s\n' "$YEL" "$N" "$1" "$DIM" "${2:-skipped}" "$N"; }
info()    { printf '    %s%s%s\n' "$DIM" "$1" "$N"; }

# Run a check: check "<name>" <command...>; stdout/stderr captured, shown on failure.
check() {
    local name="$1"; shift
    local out rc
    out=$("$@" 2>&1); rc=$?
    if [ $rc -eq 0 ]; then
        pass "$name"
        LAST_OUT="$out"; export LAST_OUT
        return 0
    fi
    fail "$name" "$(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
    LAST_OUT="$out"
    return 1
}

cleanup() {
    if [ "$DO_KEEP" -eq 1 ]; then
        [ -n "$WORKDIR" ] && info "kept workdir: $WORKDIR"
        docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" \
            && info "kept container: $CONTAINER (docker rm -f $CONTAINER)"
        return
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1
    [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ] && rm -rf "$WORKDIR"
    return 0
}
trap cleanup EXIT INT TERM

# Run llms in a throwaway container. Args are passed to the llms CLI.
run_llms() { docker run --rm --entrypoint llms "$IMAGE" "$@"; }

# Run an arbitrary command in a throwaway container.
run_in() { local cmd="$1"; shift; docker run --rm --entrypoint "$cmd" "$IMAGE" "$@"; }

printf '%sllms-py Docker image test%s\n' "$B" "$N"
printf '%simage: %s%s\n' "$DIM" "$IMAGE" "$N"

# --------------------------------------------------------- prerequisites ----

section "Prerequisites"

if ! command -v docker >/dev/null 2>&1; then
    fail "docker is installed" "docker not found on PATH"
    printf '\n%sInstall Docker: https://docs.docker.com/get-docker/%s\n' "$RED" "$N"
    exit 1
fi
pass "docker is installed ($(docker --version 2>/dev/null | head -1))"

if ! docker info >/dev/null 2>&1; then
    fail "docker daemon is running" "cannot connect to the Docker daemon"
    printf '\n%sStart Docker Desktop (or the docker service) and re-run.%s\n' "$RED" "$N"
    exit 1
fi
pass "docker daemon is running"

if docker compose version >/dev/null 2>&1; then
    pass "docker compose v2 available ($(docker compose version --short 2>/dev/null))"
elif command -v docker-compose >/dev/null 2>&1; then
    skip "docker compose v2 available" "only legacy docker-compose found"
else
    skip "docker compose v2 available" "not installed"
fi

if command -v curl >/dev/null 2>&1; then
    pass "curl is installed"
else
    fail "curl is installed" "needed for the server health tests"
    DO_SERVER=0
fi

# ------------------------------------------------------------------ pull ----

section "Image"

DIGEST_BEFORE=$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null)

if [ "$DO_PULL" -eq 1 ]; then
    if docker pull "$IMAGE" >/dev/null 2>&1; then
        pass "docker pull $IMAGE"
    else
        fail "docker pull $IMAGE" "$(docker pull "$IMAGE" 2>&1 | tail -2 | tr '\n' ' ')"
        printf '\n%sCould not pull the image — aborting.%s\n' "$RED" "$N"
        exit 1
    fi
else
    skip "docker pull $IMAGE" "--no-pull"
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    fail "image present locally" "run without --no-pull, or build it first"
    exit 1
fi
pass "image present locally"

DIGEST_AFTER=$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null)
if [ -n "$DIGEST_AFTER" ]; then
    info "digest:  ${DIGEST_AFTER#*@}"
    if [ "$DO_PULL" -eq 1 ] && [ -n "$DIGEST_BEFORE" ] && [ "$DIGEST_BEFORE" != "$DIGEST_AFTER" ]; then
        info "updated: was ${DIGEST_BEFORE#*@}"
    elif [ "$DO_PULL" -eq 1 ] && [ -n "$DIGEST_BEFORE" ]; then
        info "already up to date"
    fi
fi

IMG_CREATED=$(docker image inspect --format '{{.Created}}' "$IMAGE" 2>/dev/null)
IMG_SIZE=$(docker image inspect --format '{{.Size}}' "$IMAGE" 2>/dev/null)
IMG_ARCH=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE" 2>/dev/null)
info "created: ${IMG_CREATED%.*}"
info "size:    $(( ${IMG_SIZE:-0} / 1024 / 1024 )) MB"
info "arch:    $IMG_ARCH"

HOST_ARCH=$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null)
if [ -n "$HOST_ARCH" ] && [ "$IMG_ARCH" = "$HOST_ARCH" ]; then
    pass "image architecture matches host ($IMG_ARCH)"
elif [ -n "$HOST_ARCH" ]; then
    skip "image architecture matches host" "image $IMG_ARCH, host $HOST_ARCH — will run emulated"
fi

# Multi-arch manifest (only meaningful for a registry image)
case "$IMAGE" in
    *ghcr.io/*|*docker.io/*|*/*:*)
        PLATFORMS=$(docker buildx imagetools inspect "$IMAGE" 2>/dev/null \
                    | awk '/^ *Platform:/ {print $2}' | sort -u | tr '\n' ' ')
        if [ -n "$PLATFORMS" ]; then
            pass "multi-arch manifest published"
            info "platforms: $PLATFORMS"
            case "$PLATFORMS" in
                *linux/amd64*) : ;;
                *) fail "manifest includes linux/amd64" "got: $PLATFORMS" ;;
            esac
            case "$PLATFORMS" in
                *linux/arm64*) : ;;
                *) fail "manifest includes linux/arm64" "got: $PLATFORMS" ;;
            esac
        else
            skip "multi-arch manifest published" "buildx imagetools unavailable or local image"
        fi
        ;;
    *) skip "multi-arch manifest published" "local image" ;;
esac

# ------------------------------------------------------------ image spec ----

section "Image configuration"

IMG_USER=$(docker image inspect --format '{{.Config.User}}' "$IMAGE" 2>/dev/null)
if [ "$IMG_USER" = "llms" ] || [ "$IMG_USER" = "1000" ]; then
    pass "runs as non-root user ($IMG_USER)"
else
    fail "runs as non-root user" "Config.User='$IMG_USER'"
fi

UID_IN=$(run_in id -u 2>/dev/null | tr -d '\r')
if [ "$UID_IN" = "1000" ]; then
    pass "container uid is 1000"
else
    fail "container uid is 1000" "got '$UID_IN'"
fi

if docker image inspect --format '{{json .Config.ExposedPorts}}' "$IMAGE" 2>/dev/null | grep -q '8000/tcp'; then
    pass "exposes port 8000"
else
    fail "exposes port 8000"
fi

if docker image inspect --format '{{json .Config.Healthcheck}}' "$IMAGE" 2>/dev/null | grep -q 'urllib'; then
    pass "HEALTHCHECK is defined"
else
    fail "HEALTHCHECK is defined"
fi

IMG_CMD=$(docker image inspect --format '{{json .Config.Cmd}}' "$IMAGE" 2>/dev/null)
if printf '%s' "$IMG_CMD" | grep -q -- '--serve'; then
    pass "default CMD starts the server ($IMG_CMD)"
else
    fail "default CMD starts the server" "Cmd=$IMG_CMD"
fi

if docker image inspect --format '{{json .Config.Volumes}}' "$IMAGE" 2>/dev/null | grep -q '/home/llms/.llms'; then
    pass "declares /home/llms/.llms volume"
else
    fail "declares /home/llms/.llms volume"
fi

# -------------------------------------------------------------- toolchain ---

section "Toolchain"

if VER=$(run_in python -c "from importlib.metadata import version; print(version('llms-py'))" 2>/dev/null | tr -d '\r'); then
    if [ -n "$VER" ]; then
        pass "llms-py installed (v$VER)"
    else
        fail "llms-py installed" "version query returned nothing"
    fi
else
    fail "llms-py installed" "importlib.metadata could not find llms-py"
fi

if OUT=$(run_in python --version 2>&1 | tr -d '\r'); then
    pass "python present ($OUT)"
else
    fail "python present"
fi

if OUT=$(run_in bun --version 2>&1 | tr -d '\r'); then
    pass "bun present (v$OUT)"
else
    fail "bun present" "$OUT"
fi

if OUT=$(run_in bunx --version 2>&1 | tr -d '\r'); then
    pass "bunx present (v$OUT)"
else
    fail "bunx present" "$OUT"
fi

if OUT=$(docker run --rm --entrypoint sh "$IMAGE" -c 'dotnet --version' 2>&1 | tr -d '\r'); then
    pass "dotnet sdk present (v$OUT)"
else
    fail "dotnet sdk present" "$OUT"
fi

# dotnet aborts at startup without libicu, so this catches a missing ICU package
# even though `dotnet --version` above may have succeeded from a cached response.
if OUT=$(docker run --rm --entrypoint sh "$IMAGE" -c \
        'cd /tmp && dotnet --list-sdks' 2>&1 | tr -d '\r'); then
    case "$OUT" in
        *ICU*|*icu*) fail "dotnet globalization works (libicu present)" "$OUT" ;;
        *) pass "dotnet globalization works (libicu present)" ;;
    esac
else
    fail "dotnet globalization works (libicu present)" "$(printf '%s' "$OUT" | tail -2 | tr '\n' ' ')"
fi

if OUT=$(run_in typst --version 2>&1 | tr -d '\r'); then
    pass "typst present ($OUT)"
else
    fail "typst present" "$OUT — the PDF Studio extension disables itself without it"
fi

# Compile a real document: proves typst runs and has usable fonts.
if OUT=$(docker run --rm --entrypoint sh "$IMAGE" -c \
        'cd /tmp && printf "= Hi\n" > t.typ && typst compile t.typ t.pdf && head -c4 t.pdf' 2>&1 | tr -d '\r'); then
    case "$OUT" in
        *%PDF*) pass "typst compiles a document to PDF" ;;
        *)      fail "typst compiles a document to PDF" "unexpected output: $OUT" ;;
    esac
else
    fail "typst compiles a document to PDF" "$(printf '%s' "$OUT" | tail -2 | tr '\n' ' ')"
fi

if run_in sh -c 'command -v llms' >/dev/null 2>&1; then
    pass "llms is on PATH"
else
    fail "llms is on PATH"
fi

if run_in sh -c 'command -v git' >/dev/null 2>&1; then
    pass "git present"
else
    fail "git present"
fi

# ffmpeg is what the voice extension uses to convert the browser's recording.
if OUT=$(docker run --rm --entrypoint sh "$IMAGE" -c 'ffmpeg -version 2>&1 | head -1' 2>&1 | tr -d '\r'); then
    pass "ffmpeg present (${OUT%% https*})"
else
    fail "ffmpeg present" "voice input needs it for the voxtype/transcribe modes"
fi

# -------------------------------------------------------------------- CLI ---

section "CLI"

if OUT=$(run_llms --help 2>&1); then
    pass "llms --help"
    HELP_VER=$(printf '%s' "$OUT" | grep -o 'llms v[0-9][0-9.]*' | head -1)
    [ -n "$HELP_VER" ] && info "reports: $HELP_VER"
else
    fail "llms --help" "$(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"
fi

WORKDIR=$(mktemp -d 2>/dev/null || mktemp -d -t llms-test)
mkdir -p "$WORKDIR/config"
chmod 777 "$WORKDIR/config"

if OUT=$(docker run --rm -v "$WORKDIR/config:/home/llms/.llms" --entrypoint llms "$IMAGE" --init 2>&1); then
    pass "llms --init"
else
    fail "llms --init" "$(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"
fi

for f in llms.json providers.json; do
    if [ -s "$WORKDIR/config/$f" ]; then
        if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$WORKDIR/config/$f" 2>/dev/null; then
            pass "$f created and is valid JSON ($(wc -c < "$WORKDIR/config/$f" | tr -d ' ') bytes)"
        else
            fail "$f created and is valid JSON" "file exists but does not parse"
        fi
    else
        fail "$f created" "not found in the mounted config dir"
    fi
done

if [ -s "$WORKDIR/config/providers-extra.json" ]; then
    pass "providers-extra.json created"
else
    skip "providers-extra.json created" "not written by --init"
fi

# Provider catalogue sanity — the installer's TUI reads this same file.
if [ -s "$WORKDIR/config/providers.json" ]; then
    NPROV=$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
print(sum(1 for v in d.values() if isinstance(v,dict) and v.get('env')))
" "$WORKDIR/config/providers.json" 2>/dev/null)
    if [ -n "$NPROV" ] && [ "$NPROV" -gt 5 ] 2>/dev/null; then
        pass "providers.json lists $NPROV providers with API key env vars"
    else
        fail "providers.json lists providers with API key env vars" "found '$NPROV'"
    fi
fi

if OUT=$(docker run --rm -v "$WORKDIR/config:/home/llms/.llms" --entrypoint llms "$IMAGE" ls 2>&1); then
    pass "llms ls"
    case "$OUT" in
        *"PDF Studio disabled"*|*"typst not found"*)
            fail "PDF Studio extension is enabled" "typst missing from the image" ;;
    esac
    ENABLED=$(printf '%s' "$OUT" | grep -o 'enabled providers:.*' | head -1)
    [ -n "$ENABLED" ] && info "$ENABLED"
else
    fail "llms ls" "$(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"
fi

# ---- voice extension -------------------------------------------------------
# `api` mode should come up from a provider key alone...
OUT=$(docker run --rm -v "$WORKDIR/config:/home/llms/.llms" -e GROQ_API_KEY=test-key-not-real \
      -e VERBOSE=1 --entrypoint llms "$IMAGE" ls 2>&1 | tr -d '\r')
case "$OUT" in
    *"Using api for voice"*) pass "voice api mode activates from a provider key" ;;
    *) fail "voice api mode activates from a provider key" \
            "$(printf '%s' "$OUT" | grep -i voice | head -2 | tr '\n' ' ')" ;;
esac

# ...and a voice section in llms.json should choose the endpoint and model.
mkdir -p "$WORKDIR/voice"
cp "$WORKDIR/config/llms.json" "$WORKDIR/config/providers.json" "$WORKDIR/voice/" 2>/dev/null
python3 - "$WORKDIR/voice/llms.json" <<'PYEOF' 2>/dev/null
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg["voice"] = {"url": "http://127.0.0.1:9/v1/audio/transcriptions", "model": "test-model"}
json.dump(cfg, open(sys.argv[1], "w"), indent=4)
PYEOF
chmod -R 777 "$WORKDIR/voice" 2>/dev/null
OUT=$(docker run --rm -v "$WORKDIR/voice:/home/llms/.llms" -e VERBOSE=1 \
      --entrypoint llms "$IMAGE" ls 2>&1 | tr -d '\r')
case "$OUT" in
    *"model=test-model"*) pass "voice config is read from llms.json" ;;
    *) fail "voice config is read from llms.json" \
            "$(printf '%s' "$OUT" | grep -i voice | head -2 | tr '\n' ' ')" ;;
esac

# Optional live provider check when a key happens to be in the environment.
LIVE_PROVIDER=""; LIVE_KEY=""
for pair in "groq:GROQ_API_KEY" "openrouter:OPENROUTER_API_KEY" "google:GOOGLE_API_KEY" \
            "anthropic:ANTHROPIC_API_KEY" "openai:OPENAI_API_KEY"; do
    p="${pair%%:*}"; k="${pair#*:}"
    if [ -n "${!k:-}" ]; then LIVE_PROVIDER="$p"; LIVE_KEY="$k"; break; fi
done
if [ -n "$LIVE_PROVIDER" ]; then
    if OUT=$(docker run --rm -v "$WORKDIR/config:/home/llms/.llms" -e "$LIVE_KEY=${!LIVE_KEY}" \
             --entrypoint llms "$IMAGE" --check "$LIVE_PROVIDER" 2>&1); then
        pass "llms --check $LIVE_PROVIDER (live, using \$$LIVE_KEY)"
    else
        fail "llms --check $LIVE_PROVIDER (live)" "$(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"
    fi
else
    skip "live provider check" "no provider API key in the environment"
fi

# ----------------------------------------------------------------- server ---

if [ "$DO_SERVER" -eq 0 ]; then
    section "Server"
    skip "server tests" "--quick"
else
    section "Server"

    if [ -z "$PORT" ]; then
        for p in $(seq 18000 18050); do
            if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then PORT="$p"; break; fi
            exec 3>&- 2>/dev/null
        done
    fi
    if [ -z "$PORT" ]; then
        fail "find a free port" "18000-18050 all in use; pass --port"
    else
        info "using port $PORT"

        if docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:8000" \
             -v "$WORKDIR/config:/home/llms/.llms" "$IMAGE" >/dev/null 2>&1; then
            pass "container starts"
        else
            fail "container starts" "$(docker run --rm -v "$WORKDIR/config:/home/llms/.llms" "$IMAGE" 2>&1 | tail -3 | tr '\n' ' ')"
        fi

        # Wait for HTTP
        HTTP_OK=0
        for _ in $(seq 1 60); do
            CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/" 2>/dev/null)
            if [ "$CODE" = "200" ]; then HTTP_OK=1; break; fi
            docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || break
            sleep 1
        done
        if [ "$HTTP_OK" -eq 1 ]; then
            pass "GET / returns 200"
        else
            fail "GET / returns 200" "last status '${CODE:-none}' after 60s"
            docker logs "$CONTAINER" 2>&1 | tail -15 | sed "s/^/    ${DIM}/;s/\$/${N}/"
        fi

        if [ "$HTTP_OK" -eq 1 ]; then
            BODY=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/" 2>/dev/null)
            if printf '%s' "$BODY" | grep -qi '<html\|<!doctype'; then
                pass "/ serves the chat UI"
            else
                fail "/ serves the chat UI" "body did not look like HTML"
            fi

            CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/v1/models" 2>/dev/null)
            if [ "$CODE" = "200" ] || [ "$CODE" = "401" ]; then
                pass "/v1/models responds ($CODE)"
            else
                fail "/v1/models responds" "status $CODE"
            fi

            # Docker's own HEALTHCHECK
            HEALTH=""
            for _ in $(seq 1 45); do
                HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null)
                [ "$HEALTH" = "healthy" ] && break
                [ "$HEALTH" = "unhealthy" ] && break
                sleep 1
            done
            if [ "$HEALTH" = "healthy" ]; then
                pass "docker HEALTHCHECK reports healthy"
            elif [ -z "$HEALTH" ]; then
                skip "docker HEALTHCHECK reports healthy" "no health state"
            else
                fail "docker HEALTHCHECK reports healthy" "status '$HEALTH'"
            fi
        fi

        LOGS=$(docker logs "$CONTAINER" 2>&1)
        if printf '%s' "$LOGS" | grep -q 'Traceback (most recent call last)'; then
            fail "startup logs are free of tracebacks" "$(printf '%s' "$LOGS" | grep -A2 'Traceback' | head -3 | tr '\n' ' ')"
        else
            pass "startup logs are free of tracebacks"
        fi

        if docker stop -t 10 "$CONTAINER" >/dev/null 2>&1; then
            pass "container stops cleanly"
        else
            fail "container stops cleanly"
        fi
        [ "$DO_KEEP" -eq 0 ] && docker rm -f "$CONTAINER" >/dev/null 2>&1
    fi
fi

# ---------------------------------------------------------------- summary ---

section "Summary"
printf '  %s%d passed%s' "$GRN" "$PASSED" "$N"
[ "$FAILED"  -gt 0 ] && printf ', %s%d failed%s' "$RED" "$FAILED" "$N"
[ "$SKIPPED" -gt 0 ] && printf ', %s%d skipped%s' "$YEL" "$SKIPPED" "$N"
printf '\n'

if [ "$FAILED" -gt 0 ]; then
    printf '\n%sFailed:%s\n' "$B$RED" "$N"
    for f in "${FAILURES[@]}"; do printf '  • %s\n' "$f"; done
    printf '\n'
    exit 1
fi

printf '\n%sImage looks good.%s\n' "$GRN" "$N"
exit 0
