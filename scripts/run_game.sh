#!/usr/bin/env bash
# Usage: scripts/run_game.sh [--discord] [--game] [--port N]
# Starts the living city (--game: the 3-minute shift), plus the World Bot with --discord, and
# prints the URL. A fresh operator key is made each run. The sim gets it; in city mode so does the
# World Bot, to carry a typed leave/stay. Only the URL shows it. No agent ever has it.
# The sim never depends on Discord: if the bot stops, it keeps running. Ctrl-C stops all.
# Env: AIRGRID_DEMO_SECONDS=<n> stops after n seconds (smoke tests).
set -euo pipefail
cd "$(dirname "$0")/.."

discord=0 port=8740 profile=city
while [ "$#" -gt 0 ]; do
    case "$1" in
        --discord) discord=1 ;;
        --game) profile=game ;;
        --port) port=${2:?}; shift ;;
        *)
            sed -n 2,7p "$0" >&2
            exit 1
            ;;
    esac
    shift
done

api="http://127.0.0.1:$port"
export PYTHONIOENCODING=utf-8
if curl -s -o /dev/null -m 2 "$api/health"; then
    echo "error: something already answers on port $port" >&2
    exit 1
fi

key=$(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
mkdir -p runs
stamp=$(date +%Y%m%d-%H%M%S)
sim_log="runs/game-sim-$stamp.log"
sim_pid=
bot_pid=
stop_proc() {
    [ -n "$1" ] || return 0
    # On Git Bash `kill` can't reach the python below `uv run`, so the Windows tree is taken down.
    if [ -r "/proc/$1/winpid" ]; then
        taskkill //PID "$(cat "/proc/$1/winpid")" //T //F >/dev/null 2>&1 || true
    else
        kill "$1" 2>/dev/null || true
    fi
}
cleanup() {
    trap - EXIT INT TERM
    stop_proc "$bot_pid"
    stop_proc "$sim_pid"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# A prefix, not an export: nothing started here inherits the key.
AIRGRID_GAME_KEY="$key" uv run uvicorn sim.app:app --port "$port" --log-level warning \
    >"$sim_log" 2>&1 &
sim_pid=$!
for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 2 "$api/health" && break
    kill -0 "$sim_pid" 2>/dev/null || {
        echo "error: the sim exited; see $sim_log" >&2
        exit 1
    }
    sleep 0.5
done
curl -s -o /dev/null -m 2 "$api/health" || {
    echo "error: the sim did not answer in 30 s; see $sim_log" >&2
    exit 1
}

if [ "$profile" = city ]; then
    # The key goes by stdin, so it is never on a command line.
    printf 'X-Game-Key: %s\n' "$key" | curl -fsS -m 10 -o /dev/null -X POST "$api/city/start" \
        -H @- -H 'Content-Type: application/json' -d '{}' || {
        echo "error: the city did not start; see $sim_log" >&2
        exit 1
    }
fi

if [ "$discord" = 1 ]; then
    bot_log="runs/game-bot-$stamp.log"
    bot_key=
    [ "$profile" = city ] && bot_key=$key
    (
        AIRGRID_GAME_KEY="$bot_key" uv run python -m worldbot.bot --switch --profile "$profile" \
            --api "$api" >"$bot_log" 2>&1 ||
            echo "Discord commentary stopped, the sim runs on; see $bot_log" >&2
    ) &
    bot_pid=$!
fi

echo "Fictional district, simulated. Play at:"
echo "$api/ui/?key=$key"

if [ -n "${AIRGRID_DEMO_SECONDS:-}" ]; then
    sleep "$AIRGRID_DEMO_SECONDS"
    exit 0
fi
wait "$sim_pid"
