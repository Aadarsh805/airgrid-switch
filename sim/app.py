"""AirGrid simulator API (FastAPI), endpoints per docs/04-CONTRACTS.md §4.5 + phase03-interface.

Everything here is simulated (fictional Northgate District). The engine owns state in-process;
all engine-touching endpoints are `async def` so they run on the same loop as the runner task.
Never served: future events, the suspect path / future position, golden actions, feed, expected.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sim import actions, baseline, city, game, projections, rules, triggers
from sim.auth import bearer, identify
from sim.clock import DEFAULT_RATE, MAX_BEAT_S, SPEEDS, Clock
from sim.db import DB
from sim.engine import MODES, Engine
from sim.geo import fmt, tmin
from sim.kpi import kpi_view
from sim.scenario import ScenarioNotFound
from sim.world import load_world

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENARIO = "FRIDAY-RUSH"
DEFAULT_MODE = "demo"
AGENT_BUSY = ("busy", "working", "thinking")  # POST /control/agent-activity states
AGENT_IDLE = ("idle", "done", "posted")


def _db_path() -> str:
    return os.environ.get("AIRGRID_DB", str(ROOT / "data" / "airgrid.db"))


def state_diff(old: dict, new: dict) -> dict:
    out = {"t": new["t"], "clock": new["clock"]}
    for k, v in new.items():
        if k in ("t", "clock"):
            continue
        if isinstance(v, dict) and k in ("drones", "docks", "pilots", "incidents"):
            prev = old.get(k) or {}
            ch = {i: x for i, x in v.items() if prev.get(i) != x}
            if ch:
                out[k] = ch
        elif old.get(k) != v:
            out[k] = v
    return out


class SimServer:
    def __init__(self):
        self.world = load_world()
        self.engine: Engine | None = None
        self.clock: Clock | None = None
        self.db: DB | None = None
        self.task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self._last_state: dict | None = None
        self._last_seq = 0
        self._seen_approvals: set[str] = set()
        self.game: game.Game | None = None
        self.city: city.City | None = None

    # --- lifecycle ---
    def load(self, name: str, seed: str | None = None, mode: str = DEFAULT_MODE) -> Engine:
        self.stop_runner()
        if self.db is not None:
            self.db.close()
        self.db = DB(_db_path())
        self.game = self.city = None
        if name == city.NAME:
            scen = city.scenario(self.world, seed)
            self.engine = Engine(self.world, scen, seed=scen["seed"], mode=mode, db=self.db)
        else:
            self.engine = Engine.load(name, seed=seed, mode=mode, db=self.db, world=self.world)
        self.clock = Clock(mode)
        self._last_state = self.engine.state_view()
        self._last_seq = 0
        self._seen_approvals = set()
        self._broadcast({"event": "snapshot", "data": json.dumps(self._last_state)})
        return self.engine

    def ensure(self) -> Engine:
        if self.engine is None:
            self.load(DEFAULT_SCENARIO, None, DEFAULT_MODE)
        return self.engine

    def stop_runner(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    def runner_alive(self) -> bool:
        return self.task is not None and not self.task.done()

    # --- publishing ---
    def _broadcast(self, item: dict) -> None:
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(item)

    def publish(self) -> None:
        eng = self.engine
        for e in eng.log.since(seq=self._last_seq):
            self._broadcast({"event": "event", "data": json.dumps(e.to_dict(), ensure_ascii=False)})
            self._last_seq = e.seq
        for a in eng.approvals.items.values():  # new cards for the World Bot; not log events
            if a.id not in self._seen_approvals:
                self._seen_approvals.add(a.id)
                frame = {**a.view(), "t": round(a.created_min, 4)}
                data = json.dumps(frame, ensure_ascii=False)
                self._broadcast({"event": "approval", "data": data})
        new = eng.state_view()
        self._broadcast({"event": "tick", "data": json.dumps(state_diff(self._last_state, new))})
        self._last_state = new

    def sync_holds(self) -> None:
        """Demo/live clock holds while any hold_clock approval is pending."""
        if self.clock is None or self.engine is None:
            return
        pending = {f"approval:{a.id}" for a in self.engine.approvals.pending() if a.hold_clock}
        for key in list(self.clock._holds):
            if key.startswith("approval:") and key not in pending:
                self.clock.release(key)
        for key in pending:
            if key not in self.clock._holds:
                self.clock.hold(key, None, approval=True)

    def after_write(self) -> None:
        if self.db is not None:
            self.db.sync(self.engine)
        self.sync_holds()
        self.publish()

    def start_game(self, paused: bool = False) -> game.Game:
        self.load(DEFAULT_SCENARIO, None, "game")
        self.game = game.Game(self.engine)
        self.game.start()
        if self.db is not None:
            self.db.sync(self.engine)
        self.publish()
        if not paused:
            self.clock.start()
            self.task = asyncio.create_task(self.run_loop())
        return self.game

    def start_city(self, seed: str | None = None, paused: bool = False) -> city.City:
        self.load(city.NAME, seed, "city")
        self.city = city.City(self.engine)
        self.city.start()
        self.clock.rate = self.world.raw["city"]["sim_min_per_s"]
        if self.db is not None:
            self.db.sync(self.engine)
        self.publish()
        if not paused:
            self.clock.start()
            self.task = asyncio.create_task(self.run_loop())
        return self.city

    def sync_game(self) -> None:
        """Game clock: hold at a prompt, auto-skip dead time between beats."""
        g = self.game
        g.poll()
        fly_rate = self.world.policies["game_fly_sim_min_per_s"]
        self.clock.rate = fly_rate if g.flight is not None else DEFAULT_RATE
        if g.prompt is not None:
            self.clock.hold("game:prompt", None, approval=True)
            return
        self.clock.release("game:prompt")
        target = g.skip_target()
        if target is not None and self.clock.skip_target is None:
            self.clock.skip_to(target)

    def advance_to(self, target: float, stop_at_actions: bool = False) -> None:
        if self.game is not None:
            self.game.run_until(target)
        elif stop_at_actions:
            self.engine.run_to_actions(target)
        else:
            self.engine.run_until(target)
        self.publish()

    async def run_loop(self) -> None:
        eng, clock = self.engine, self.clock
        step = self.game.step if self.game is not None else eng.step
        while not eng.done:
            self.sync_holds()
            if self.game is not None:
                self.sync_game()
            n = clock.ticks_due(eng._t)
            per_tick = n <= 5
            for _ in range(n):
                if eng.done:
                    break
                step()
                if per_tick:
                    self.publish()
            if n and not per_tick:
                self.publish()
            await asyncio.sleep(0 if clock.mode == "headless" else 0.05)


S = SimServer()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    S.stop_runner()


app = FastAPI(
    title="AirGrid simulator (simulated, fictional Northgate District)", lifespan=lifespan
)


def _not_found(reason: str, hint: str = "GET /world, /fleet, /docks, /pilots") -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": "NOT_FOUND", "reason": reason, "hint": hint}, status_code=404
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _not_found(f"{request.url.path} not found")
    return JSONResponse(
        {"ok": False, "error": "request failed", "reason": str(exc.detail), "hint": ""},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", ()))
    return _bad(f"{where}: {first.get('msg', 'invalid')}", "see docs/04-CONTRACTS.md §4.5")


def _bad(reason: str, hint: str = "see docs/04-CONTRACTS.md §4.5") -> JSONResponse:
    """Malformed request: HTTP 422, error shape (no §4.2 reason code is invented)."""
    return JSONResponse(
        {"ok": False, "error": "invalid request", "reason": reason, "hint": hint}, status_code=422
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "airgrid-sim", "simulated": True}


@app.get("/world")
async def world() -> dict:
    return {"ok": True, **S.world.geometry()}


@app.get("/state")
async def state() -> dict:
    return {"ok": True, **S.ensure().state_view()}


@app.get("/status")
async def status() -> dict:
    eng = S.ensure()
    return {
        "ok": True,
        "simulated": True,
        "scenario": eng.name,
        "title": eng.title,
        "seed": eng.seed,
        "mode": eng.mode,
        "clock": fmt(eng.now),
        "t": round(eng.now, 4),
        "start": fmt(eng.T0),
        "end": fmt(eng.T1),
        "done": eng.done,
        "runner": S.runner_alive(),
        **{f"clock_{k}": v for k, v in S.clock.view().items() if k != "mode"},
        "pending_approvals": len(eng.approvals.pending()),
        "standing_policy": eng.standing_policy,
        "kpi": eng.kpi.strip(eng),
    }


@app.get("/fleet")
async def fleet() -> dict:
    eng = S.ensure()
    drones = [{**d.view(), **eng.landing(d)} for d in eng.drones.values()]
    return {"ok": True, "clock": fmt(eng.now), "drones": drones}


@app.get("/fleet/{drone_id}")
async def fleet_one(drone_id: str):
    eng = S.ensure()
    d = eng.drones.get(drone_id.upper())
    if d is None:
        return _not_found(f"no drone {drone_id}", "airgrid fleet")
    return {"ok": True, "clock": fmt(eng.now), "drone": {**d.view(), **eng.landing(d)}}


@app.get("/docks")
async def docks() -> dict:
    eng = S.ensure()
    free = sum(k.free_slots() for k in eng.docks.values())
    return {
        "ok": True,
        "clock": fmt(eng.now),
        "slots_free": free,
        "slots_total": sum(k.slots for k in eng.docks.values()),
        "docks": [k.view() for k in eng.docks.values()],
    }


@app.get("/pilots")
async def pilots() -> dict:
    eng = S.ensure()
    return {"ok": True, "clock": fmt(eng.now), "pilots": [p.view() for p in eng.pilots.values()]}


@app.get("/events")
async def events(since: str | None = None):
    eng = S.ensure()
    t = None
    if since:
        try:
            t = tmin(since)
        except (ValueError, IndexError):
            return _bad("since must be HH:MM")
    return {"ok": True, "clock": fmt(eng.now), "events": [e.to_dict() for e in eng.log.since(t)]}


@app.get("/events/stream")
async def events_stream(request: Request):
    eng = S.ensure()
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    S.subscribers.add(q)
    first = json.dumps(eng.state_view())

    async def gen():
        try:
            yield {"event": "snapshot", "data": first}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=1.0)
                except TimeoutError:
                    continue
                yield item
        finally:
            S.subscribers.discard(q)

    return EventSourceResponse(gen(), ping=15)


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid__": True}
    return body if isinstance(body, dict) else {"__invalid__": True}


def _operator(op: str, detail: str) -> None:
    """Operator controls (dashboard/localhost, no token) still leave a decisions_log row."""
    actions.log_decision(S.engine, "operator", op, rationale=detail[:300])
    if S.db is not None:
        S.db.sync(S.engine)


@app.post("/control/{action}")
async def control(action: str, request: Request):
    resp = await _control(action, request)
    if action in ("start", "pause", "resume", "reset", "speed", "skip"):
        status = resp.status_code if isinstance(resp, JSONResponse) else 200
        body = (await request.body()).decode("utf-8", "replace")
        _operator(f"control:{action}", f"{body or '{}'} -> HTTP {status}")
    return resp


async def _control(action: str, request: Request):
    eng = S.ensure()
    clock = S.clock
    body = await _json_body(request)
    if body.get("__invalid__"):
        return _bad("body must be a JSON object")
    if action == "start":
        if not clock.running:
            clock.start()
        if not S.runner_alive() and not eng.done:
            S.task = asyncio.create_task(S.run_loop())
    elif action == "pause":
        clock.pause()
    elif action == "resume":
        clock.resume()
        if not S.runner_alive() and not eng.done:
            S.task = asyncio.create_task(S.run_loop())
    elif action == "reset":
        if S.game is not None:
            S.start_game()
        elif S.city is not None:
            S.start_city(eng.seed)
        else:
            S.load(eng.name, eng.seed, eng.mode)
    elif action == "speed":
        try:
            clock.set_speed(body.get("sim_min_per_s"))
        except (TypeError, ValueError):
            return _bad(f"sim_min_per_s must be one of {SPEEDS}")
    elif action == "skip":
        if "to" in body:
            try:
                target = tmin(body["to"])
            except (ValueError, IndexError, TypeError):
                return _bad("to must be HH:MM")
            S.advance_to(target, body.get("stop") == "actions")
        else:
            nxt = eng.next_event_time()
            if nxt is not None:
                if S.runner_alive() and clock.running and clock.mode != "headless":
                    clock.skip_to(nxt)  # fast-forward at 10 sim-min/s (target not disclosed)
                else:
                    S.advance_to(nxt)
    elif action == "agent-activity":
        agent, state = body.get("agent"), body.get("state")
        if not isinstance(agent, str) or not agent or state not in AGENT_BUSY + AGENT_IDLE:
            return _bad(f"needs agent and state in {AGENT_BUSY + AGENT_IDLE}")
        if S.city is not None:
            pass  # agents never gate the city (L56)
        elif state in AGENT_BUSY:
            cap = eng.world.policies["game_clock"]["agent_hold_s"]
            clock.hold(f"agent:{agent}", cap if S.game is not None else MAX_BEAT_S)
        else:
            clock.release(f"agent:{agent}")
    else:
        return _not_found(
            f"no control action {action}", "start|pause|resume|reset|speed|skip|agent-activity"
        )
    eng = S.engine
    return {
        "ok": True,
        "action": action,
        "clock": fmt(eng.now),
        "done": eng.done,
        **S.clock.view(),
    }


class LoadBody(BaseModel):
    name: str = DEFAULT_SCENARIO
    seed: str | None = None
    mode: str = DEFAULT_MODE


@app.post("/scenario/load")
async def scenario_load(body: LoadBody):
    if body.mode not in MODES:
        return _bad(f"mode must be one of {MODES}")
    try:
        eng = S.load(body.name, body.seed, body.mode)
    except ScenarioNotFound:
        return _not_found(f"no scenario {body.name}", "FRIDAY-RUSH | QUIET-SHIFT")
    return {
        "ok": True,
        "scenario": eng.name,
        "seed": eng.seed,
        "mode": eng.mode,
        "clock": fmt(eng.now),
    }


# ---------------------------------------------------------------- Phase 03: writes
def _identity(request: Request):
    return identify(bearer(request.headers.get("authorization")))


def _out(o: actions.Outcome) -> JSONResponse:
    S.after_write()
    return JSONResponse(o.body(), status_code=o.status)


class ActionBody(BaseModel):
    role: str | None = None
    action: str
    args: dict = {}


@app.post("/actions")
async def post_action(body: ActionBody, request: Request):
    eng = S.ensure()
    try:
        o = actions.execute(eng, _identity(request), body.action, body.args, role=body.role)
    except actions.InvalidRequest as e:
        return _bad(str(e), f"see docs/phase03-interface.md for '{body.action}' args")
    return _out(o)


class ProposalBody(BaseModel):
    type: str
    args: dict = {}
    summary: str = ""


@app.post("/approvals")
async def post_approval(body: ProposalBody, request: Request):
    eng = S.ensure()
    try:
        o = actions.propose(eng, _identity(request), body.type, body.args, body.summary)
    except actions.InvalidRequest as e:
        return _bad(str(e), "see docs/phase03-interface.md (approvals)")
    return _out(o)


async def _decide(approval_id: str, request: Request, approve: bool):
    eng = S.ensure()
    body = await _json_body(request)
    if body.get("__invalid__"):
        return _bad("body must be a JSON object")
    note = body.get("note") if approve else body.get("reason")
    o = actions.decide(eng, _identity(request), approval_id, approve, note or "")
    return _out(o)


@app.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, request: Request):
    return await _decide(approval_id, request, True)


@app.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, request: Request):
    return await _decide(approval_id, request, False)


@app.get("/approvals")
async def get_approvals(status: str | None = None):
    eng = S.ensure()
    if status not in (None, "pending", "approved", "rejected"):
        return _bad("status must be pending, approved or rejected")
    return {
        "ok": True,
        "clock": fmt(eng.now),
        "approvals": [a.view() for a in eng.approvals.by_status(status)],
    }


def _since(since: str | None):
    return tmin(since) if since else None


@app.get("/privacy-log")
async def privacy_log(since: str | None = None):
    eng = S.ensure()
    try:
        t = _since(since)
    except (ValueError, IndexError):
        return _bad("since must be HH:MM")
    return {"ok": True, "clock": fmt(eng.now), "privacy_log": eng.privacy.rows(t)}


@app.get("/decisions")
async def decisions(since: str | None = None):
    eng = S.ensure()
    try:
        t = _since(since)
    except (ValueError, IndexError):
        return _bad("since must be HH:MM")
    rows = [r for r in eng.decisions if t is None or r["sim_min"] >= t - 1e-9]
    return {"ok": True, "clock": fmt(eng.now), "decisions": rows}


@app.get("/incidents")
async def incidents():
    eng = S.ensure()
    return {
        "ok": True,
        "clock": fmt(eng.now),
        "incidents": [eng.incident_view(k) for k in eng.incidents],
    }


@app.get("/incidents/{incident_id}")
async def incident(incident_id: str):
    eng = S.ensure()
    if incident_id not in eng.incidents:
        return _not_found(f"no incident {incident_id}", "airgrid incidents")
    return {"ok": True, "clock": fmt(eng.now), "incident": eng.incident_view(incident_id)}


class TriggerBody(BaseModel):
    kind: str
    at: object = None  # optional [x, y] or landmark overriding the menu's location


@app.post("/trigger")
async def trigger(body: TriggerBody):
    """Live trigger menu (scenarios/triggers.yaml). Everything happens at "now" (L33)."""
    eng = S.ensure()
    if body.kind not in triggers.menu():
        _operator(f"trigger:{body.kind}", "unknown trigger -> HTTP 404")
        return _not_found(f"no trigger {body.kind}", ", ".join(triggers.menu()))
    try:
        triggers.parse_at(eng.world, body.at)
    except triggers.InvalidAt as e:
        _operator(f"trigger:{body.kind}", f"{e} -> HTTP 422")
        return _bad(str(e), "at: [x, y] inside the district, or a landmark name")
    if eng.done:
        rej = rules.shift_over(f"trigger {body.kind}")
        actions.log_decision(eng, "operator", f"trigger:{body.kind}", rej.code, rej.reason)
        if S.db is not None:
            S.db.sync(eng)
        return JSONResponse(rej.body(), status_code=409)
    result = triggers.fire(eng, body.kind, at=body.at)
    actions.log_decision(eng, "operator", f"trigger:{body.kind}", rationale=str(result)[:300])
    S.after_write()
    return {"ok": True, "result": {"kind": body.kind, **result}}


@app.get("/units")
async def units():
    eng = S.ensure()
    return {"ok": True, "simulated": True, "clock": fmt(eng.now), "units": eng.ground.view(eng.now)}


@app.get("/airspace")
async def airspace():
    eng = S.ensure()
    return {
        "ok": True,
        "clock": fmt(eng.now),
        "zones": eng.airspace.view(),
        "weather": eng.weather.view(),
        "grounded": sorted(eng.weather.grounded),
        "intrusions": [
            {"sector": x["sector"], "at": x["at"], "until": fmt(x["until"])} for x in eng.intrusions
        ],
    }


def _projection(fn, *args):
    """Read-only projections of the present state (never logged, never a refusal KPI)."""
    try:
        out = fn(S.ensure(), *args)
    except actions.InvalidRequest as e:
        return _bad(str(e))
    if isinstance(out, rules.Rejection):
        return JSONResponse(out.body(), status_code=404 if out.code == "NOT_FOUND" else 409)
    return {"ok": True, "simulated": True, "clock": fmt(S.engine.now), **out}


@app.get("/plan/rotation")
async def plan_rotation(loop: str | None = None):
    return _projection(projections.rotation_plan, loop)


@app.get("/route/check")
async def route_check(drone: str, to: str):
    return _projection(projections.route_check, drone, to)


@app.get("/track/status")
async def track_status(incident: str):
    return _projection(projections.track_status, incident)


@app.get("/relay/plan")
async def relay_plan(incident: str):
    return _projection(projections.relay_plan, incident)


@app.get("/kpi")
async def kpi():
    eng = S.ensure()
    return {"ok": True, "simulated": True, "clock": fmt(eng.now), **kpi_view(eng)}


@app.get("/kpi/history")
async def kpi_history():
    eng = S.ensure()
    return {"ok": True, "clock": fmt(eng.now), "history": list(eng.kpi_history)}


@app.post("/baseline/run")
async def baseline_run():
    eng = S.ensure()
    eng.baseline_result = baseline.run(eng.world, eng.scenario)
    _operator("baseline:run", eng.name)
    return {"ok": True, "simulated": True, "clock": fmt(eng.now), **kpi_view(eng)}


# ---------------------------------------------------------------- game mode (L54)
def _game_denied(request: Request) -> JSONResponse | None:
    """X-Game-Key against env AIRGRID_GAME_KEY. No key configured: every /game call is refused."""
    want = os.environ.get("AIRGRID_GAME_KEY", "")
    got = request.headers.get("x-game-key", "")
    if want and hmac.compare_digest(want.encode(), got.encode()):
        return None
    rej = rules.forbidden_role("caller without the operator key", "game mode")
    if S.engine is not None:
        actions.log_decision(S.engine, "anonymous", request.url.path, rej.code, rej.reason)
    return JSONResponse(rej.body(), status_code=403)


def _game_body(view: dict) -> dict:
    return {"ok": True, "simulated": True, **view}


def _no_game() -> JSONResponse:
    return _not_found("no game running", "POST /game/start")


def _game_error(e: game.GameError) -> JSONResponse:
    return JSONResponse(e.rejection.body(), status_code=409)


class StartBody(BaseModel):
    paused: bool = False  # true: nothing advances by itself (headless use and tests)


@app.post("/game/start")
async def game_start(request: Request, body: StartBody | None = None):
    if denied := _game_denied(request):
        return denied
    g = S.start_game(paused=bool(body and body.paused))
    return _game_body(g.view())


@app.get("/game")
async def game_get(request: Request):
    if denied := _game_denied(request):
        return denied
    if S.game is None:
        return _game_body(game.idle_view())
    S.game.poll()
    return _game_body(S.game.view())


class ChooseBody(BaseModel):
    choice: str


@app.post("/game/choose")
async def game_choose(body: ChooseBody, request: Request):
    if denied := _game_denied(request):
        return denied
    if S.game is None:
        return _no_game()
    try:
        S.game.poll()
        S.game.choose(body.choice)
    except game.GameError as e:
        return _game_error(e)
    except ValueError as e:
        return _bad(str(e), "GET /game lists the options")
    S.after_write()
    return _game_body(S.game.view())


class FlyBody(BaseModel):
    dx: float
    dy: float


@app.post("/game/fly")
async def game_fly(body: FlyBody, request: Request):
    if denied := _game_denied(request):
        return denied
    if S.game is None:
        return _no_game()
    if not game.valid_direction(body.dx, body.dy):
        return _bad("dx, dy must be a unit vector or 0,0")
    try:
        return _game_body({"flight": S.game.fly(body.dx, body.dy), "clock": fmt(S.engine.now)})
    except game.GameError as e:
        return _game_error(e)


@app.get("/game/score")
async def game_score(request: Request):
    if denied := _game_denied(request):
        return denied
    if S.game is None:
        return _no_game()
    return _game_body(S.game.score_view())


# ---------------------------------------------------------------- city mode (L56)
def _city_error(e: city.CityError) -> JSONResponse:
    status = 404 if e.rejection.code == "NOT_FOUND" else 409
    return JSONResponse(e.rejection.body(), status_code=status)


class CityStartBody(BaseModel):
    seed: str | None = None
    paused: bool = False  # true: nothing advances by itself (headless use and tests)


@app.post("/city/start")
async def city_start(request: Request, body: CityStartBody | None = None):
    if denied := _game_denied(request):
        return denied
    c = S.start_city(body.seed if body else None, bool(body and body.paused))
    return _game_body(c.view())


def _is_operator(request: Request) -> bool:
    """The operator key, checked quietly: a keyless poll is normal (agents, CLI), not an attempt."""
    want = os.environ.get("AIRGRID_GAME_KEY", "")
    got = request.headers.get("x-game-key", "")
    return bool(want) and hmac.compare_digest(want.encode(), got.encode())


@app.get("/city")
async def city_get(request: Request, since: int = 0):
    if S.city is None:
        return _game_body(city.idle_view(S.world))
    return _game_body(S.city.view(since, operator=_is_operator(request)))


class CityTriggerBody(BaseModel):
    kind: str


@app.post("/city/trigger")
async def city_trigger(body: CityTriggerBody, request: Request):
    if denied := _game_denied(request):
        return denied
    if S.city is None:
        return _not_found("no city running", "POST /city/start")
    if body.kind == "random_fire":  # hidden until a drone finds it: nothing about it here
        out = S.city.random_fire()
        S.after_write()
        return _game_body({"incident": None, **out})
    try:
        result = S.city.trigger(body.kind)
    except city.CityError as e:
        return _city_error(e)
    S.after_write()
    return _game_body({"incident": result})


class CityDecideBody(BaseModel):
    incident: str | None = None
    place: str | None = None  # a fragment of the incident's name, e.g. "market"
    choice: str


@app.post("/city/decide")
async def city_decide(body: CityDecideBody, request: Request):
    if denied := _game_denied(request):
        return denied
    if S.city is None:
        return _not_found("no city running", "POST /city/start")
    if not body.incident and not body.place:
        return _bad("needs incident or place")
    try:
        result = S.city.decide(body.incident, body.choice, place=body.place)
    except city.CityError as e:
        return _city_error(e)
    except ValueError as e:
        return _bad(str(e), "choice: leave | stay, or transport | treat on a doctor's call")
    S.after_write()
    return _game_body(result)


# Replay fixtures hold a whole golden run, future included: never from the live sim (rule 8).
@app.get("/ui/fixtures/{path:path}")
async def no_fixtures(path: str):
    return _not_found("replay fixtures are not served by the live sim", "use a static host")


# Dashboard lane (Phase 07) owns dashboard/; mounted here once so that lane never edits sim/.
app.mount("/ui", StaticFiles(directory=ROOT / "dashboard", html=True, check_dir=False), name="ui")
