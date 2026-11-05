#!/usr/bin/env bash
# Usage: scripts/run_demo.sh [--fake-room | --switch] [SCENARIO] [--port N] [--speed X] [--no-start]
# Starts the sim (dashboard static files at /ui) and the World Bot in the foreground. Default
# --fake-room: type `kazum: approve A-0001` here. Ctrl-C stops both.
# Env: AIRGRID_DEMO_SECONDS=<n> stops after n seconds (smoke tests).
set -euo pipefail
cd "$(dirname "$0")/.."

room=--fake-room scenario=FRIDAY-RUSH port=8740 speed=0.5 start=1
usage() {
    sed -n 2,5p "$0" >&2
    exit 1
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --fake-room | --switch) room=$1 ;;
        --port) port=${2:?}; shift ;;
        --speed) speed=${2:?}; shift ;;
        --no-start) start=0 ;;
        -*) usage ;;
        *) scenario=$1 ;;
    esac
    shift
done

api="http://127.0.0.1:$port"
export PYTHONIOENCODING=utf-8 AIRGRID_API_URL="$api"
if curl -s -o /dev/null -m 2 "$api/health"; then
    echo "error: something already answers on port $port" >&2
    exit 1
fi

mkdir -p runs
sim_log="runs/demo-sim-$(date +%Y%m%d-%H%M%S).log"
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

uv run uvicorn sim.app:app --port "$port" --log-level warning >"$sim_log" 2>&1 &
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

curl -fsS -m 10 -o /dev/null -X POST "$api/scenario/load" -H 'Content-Type: application/json' \
    -d "{\"name\": \"$scenario\", \"mode\": \"demo\"}" || {
    echo "error: scenario $scenario did not load" >&2
    exit 1
}
if [ "$start" = 1 ]; then
    curl -fsS -m 10 -o /dev/null -X POST "$api/control/start" -H 'Content-Type: application/json' \
        -d "{\"speed\": $speed}"
fi
echo "sim $api · dashboard $api/ui · log $sim_log · $scenario · room $room (simulated)"

if [ -n "${AIRGRID_DEMO_SECONDS:-}" ]; then
    uv run python -m worldbot.bot "$room" --api "$api" &
    bot_pid=$!
    for _ in $(seq 1 "$AIRGRID_DEMO_SECONDS"); do
        kill -0 "$bot_pid" 2>/dev/null || break
        sleep 1
    done
    exit 0
fi
uv run python -m worldbot.bot "$room" --api "$api"
