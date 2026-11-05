# 11 · Tools Stack

## Must have
| What | For |
|---|---|
| Claude Code | building (builder subagents) and running most runtime agents |
| Switch Console + Docker Desktop | runs the agents, connects Discord (Docker is Switch's, not ours) |
| Switch docs MCP | `claude mcp add switch-docs --transport http https://docs.switchagents.ai/mcp` — every Switch detail comes from here |
| Discord | the ops centre rooms |
| GitHub + `gh` | the repo judges clone |
| Python 3.12 + `uv` | FastAPI, sse-starlette, Pydantic v2, SQLite (stdlib), Typer, httpx, PyYAML, pytest, ruff |
| `jq` | the ruff auto-format hook in `.claude/settings.json` |
| Plain HTML/JS/SVG | dashboard; no framework, no build |
| OBS + CapCut/Clipchamp | master recording, trim, ⏩ markers |

## Nice to have (add one at a time)
- **Codex CLI** (second provider for Fleet/Airspace/Relay): `codex mcp add switch-docs --url https://docs.switchagents.ai/mcp`. Cut item 1.
- **Playwright MCP** — dashboard screenshots at gates (Chromium is also scriptable from Python).
- **Context7 MCP** — current FastAPI / sse-starlette docs.
- **`frontend-design` skill** — the dashboard visual pass in Phase 07.
- Mermaid — architecture diagram in README.

## Simulation-specific
Nothing extra. The sim is ~1,500 lines of plain Python: a tick loop, geometry helpers, YAML loading. `tools/fleet_check.py` is the reference model to port from.

## Skip
SimPy, Faker, Mesa, real map tiles (Leaflet/Mapbox), Streamlit (fights a 1 s animated map), React/Next, LangGraph/CrewAI/AutoGen, Docker Compose for our services, any CV/face library, paid assets.

## Human skills (solo)
kazum: read Python enough to sanity-check sim output, Switch Console setup, narration, deciding at gates, pushing the bad requests on camera with a straight face.

## Security
MCPs and plugins run code locally: official sources only. Never paste keys into prompts. Tokens in `config/tokens.local.yaml` (gitignored). Runtime agents live in `~/airgrid-agents/`, outside the repo (L11).
