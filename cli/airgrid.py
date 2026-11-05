"""`airgrid` CLI the agents call (docs/04-CONTRACTS.md §4.1). A thin client: no rules, no state.

Env: AIRGRID_API_URL, AIRGRID_ROLE, AIRGRID_TOKEN.
Exit 0 ok · 2 rejected ({"ok":false,"code","reason","hint"}) · 1 error (unreachable, malformed,
5xx). The token is only ever sent as a header: never printed, never in an error message.
"""

from __future__ import annotations

import json
import os
import sys
from contextvars import ContextVar
from typing import Annotated

import httpx
import typer
from typer.core import TyperCommand, TyperGroup

try:
    from typer._click.exceptions import UsageError  # newer Typer vendors click
except ImportError:
    from click.exceptions import UsageError

DEFAULT_API = "http://localhost:8740"
TIMEOUT_S = 10
# §4.2. Kept here on purpose: agents get the CLI, never the simulator package (L11).
CODES = (
    "BATTERY_RESERVE",
    "DRONE_NOT_READY",
    "NO_FLY_ZONE",
    "WEATHER_GROUNDED",
    "DOCK_FULL",
    "DOCK_OFFLINE",
    "PILOT_SATURATED",
    "SEPARATION",
    "OUT_OF_RANGE",
    "AUTH_REQUIRED",
    "PRIVACY_POLICY",
    "MISSION_EXPIRED",
    "FORBIDDEN_ROLE",
    "NOT_FOUND",
)
REJECTION_STATUS = (401, 403, 404, 409)
TRANSPORT_ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError)

_simulating: ContextVar[bool] = ContextVar("simulating", default=False)


def _api() -> str:
    return os.environ.get("AIRGRID_API_URL", DEFAULT_API).rstrip("/")


def _headers() -> dict:
    tok = os.environ.get("AIRGRID_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _role() -> str | None:
    return os.environ.get("AIRGRID_ROLE") or None


def _print(payload: dict, text: bool, line: str | None) -> None:
    out = line if text and line else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    typer.echo(out)


def _fail(reason: str, text: bool, hint: str = "") -> typer.Exit:
    body = {"ok": False, "error": "error", "reason": reason, "hint": hint}
    _print(body, text, f"error: {reason}")
    return typer.Exit(1)


def _unreachable(e: Exception, text: bool) -> typer.Exit:
    # the type only: some transport errors quote the offending header value
    return _fail(f"cannot reach sim at {_api()} ({type(e).__name__})", text)


def _request(method: str, path: str, **kw) -> httpx.Response:
    with httpx.Client(timeout=TIMEOUT_S) as c:
        return c.request(method, f"{_api()}{path}", headers=_headers(), **kw)


def _is_rejection(status: int, body) -> bool:
    return status in REJECTION_STATUS and isinstance(body, dict) and body.get("code") in CODES


def _reject(body: dict, text: bool) -> typer.Exit:
    line = f"rejected {body['code']}: {body.get('reason', '')} | {body.get('hint', '')}"
    _print(body, text, line)
    return typer.Exit(2)


def _call(method: str, path: str, text: bool, render=None, **kw) -> dict:
    """One request; prints the outcome and exits unless it was a 2xx."""
    try:
        r = _request(method, path, **kw)
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e, text) from None
    try:
        body = r.json()
    except ValueError:
        body = None
    if 200 <= r.status_code < 300 and isinstance(body, dict):
        _print(body, text, render(body) if render else _one_line(body))
        return body
    if _is_rejection(r.status_code, body):
        raise _reject(body, text)
    reason = body.get("reason") if isinstance(body, dict) else None
    raise _fail(reason or f"HTTP {r.status_code}", text, f"HTTP {r.status_code}")


def _one_line(body: dict) -> str:
    """Scalars of the result (or the body), key=value."""
    src = body.get("result") if isinstance(body.get("result"), dict) else body
    parts = [
        f"{k}={v}"
        for k, v in src.items()
        if isinstance(v, str | int | float | bool) and k not in ("ok", "simulated")
    ]
    return "ok " + " ".join(parts)


def _no_read_in_simulate(text: bool) -> None:
    if _simulating.get():
        raise _fail("simulate takes a write, not a read", text)


def _get(path: str, text: bool, render=None, params: dict | None = None) -> dict:
    _no_read_in_simulate(text)
    query = {k: v for k, v in (params or {}).items() if v is not None}
    return _call("GET", path, text, render, params=query)


def _write(action: str, args: dict, text: bool) -> None:
    args = {k: v for k, v in args.items() if v is not None}
    if _simulating.get():
        args, action = {"action": action, "args": args}, "simulate"
    _call("POST", "/actions", text, json={"role": _role(), "action": action, "args": args})


def _propose(type_: str, args: dict, summary: str, text: bool) -> None:
    if _simulating.get():
        inner = {"type": type_, "args": args, "summary": summary}
        body = {"role": _role(), "action": "simulate", "args": {"action": "propose", "args": inner}}
        _call("POST", "/actions", text, json=body)
        return
    _call("POST", "/approvals", text, json={"type": type_, "args": args, "summary": summary})


class _Root(TyperGroup):
    """Bad usage is a malformed call (exit 1): click's own 2 would read as a rejection."""

    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except UsageError as e:
            raise _fail(e.format_message(), False, "airgrid --help") from None

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except UsageError as e:
            raise _fail(e.format_message(), False, "airgrid --help") from None


class _MessageCommand(TyperCommand):
    """§4.1 gives --text two jobs: the message on `propose broadcast` and `advisory`, the one-line
    switch everywhere. On these two commands the first is the message, a second the switch."""

    def parse_args(self, ctx, args):
        at = [i for i, a in enumerate(args) if a == "--text"]
        if len(at) > 1:
            args = list(args)
            args[at[-1]] = "--one-line"
        return super().parse_args(ctx, args)


app = typer.Typer(add_completion=False, no_args_is_help=True, cls=_Root)
incident_app = typer.Typer(no_args_is_help=True)
propose_app = typer.Typer(no_args_is_help=True)
plan_app = typer.Typer(no_args_is_help=True)
route_app = typer.Typer(no_args_is_help=True)
evidence_app = typer.Typer(no_args_is_help=True)
track_app = typer.Typer(no_args_is_help=True)
relay_app = typer.Typer(no_args_is_help=True)
privacy_app = typer.Typer(no_args_is_help=True)
unit_app = typer.Typer(no_args_is_help=True)
app.add_typer(incident_app, name="incident", help="open / close")
app.add_typer(propose_app, name="propose", help="sensitive actions: a card for a human")
app.add_typer(plan_app, name="plan", help="read-only plans")
app.add_typer(route_app, name="route", help="route checks")
app.add_typer(evidence_app, name="evidence", help="evidence tags")
app.add_typer(track_app, name="track", help="track status")
app.add_typer(relay_app, name="relay", help="relay plan")
app.add_typer(privacy_app, name="privacy", help="privacy check")
app.add_typer(unit_app, name="unit", help="ground units")

Text = Annotated[bool, typer.Option("--text", help="one line instead of JSON")]
OneLine = Annotated[bool, typer.Option("--one-line", help="same as a second --text", hidden=True)]
Drone = Annotated[str, typer.Option("--drone")]
Incident = Annotated[str, typer.Option("--incident")]
Since = Annotated[str | None, typer.Option("--since", help="HH:MM")]
Pilot = Annotated[str | None, typer.Option("--pilot", help="default: the remote pilot")]
Why = Annotated[str | None, typer.Option("--why")]


@app.callback()
def main() -> None:
    """AirGrid CLI (simulated drone network, fictional district)."""
    for stream in (sys.stdout, sys.stderr):  # event text has emoji; Windows consoles default cp1252
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- reads
def _status_line(b: dict) -> str:
    k = b.get("kpi", {})
    return (
        f"{b['clock']} {b['mode']} {b['scenario']} | pending {b['pending_approvals']} | "
        f"airborne {k.get('airborne')} ready {k.get('ready')} slots {k.get('slots_free')} "
        f"coverage {k.get('coverage_pct')}% flights {k.get('flights')} (simulated)"
    )


def _count_line(key: str):
    def render(b: dict) -> str:
        return f"{b.get('clock', '')} {len(b.get(key) or [])} {key}".strip()

    return render


@app.command()
def status(text: Text = False) -> None:
    """Clock, mode, scenario, pending approvals, KPI strip."""
    _get("/status", text, _status_line)


@app.command()
def fleet(
    id_: Annotated[str | None, typer.Option("--id")] = None,
    text: Text = False,
) -> None:
    """Drones: state, battery, pos, mission, pilot, slot booked."""
    if id_:
        _get(
            f"/fleet/{id_}",
            text,
            lambda b: "{id} {state} {battery}% {mission} pilot {pilot}".format(**b["drone"]),
        )
        return

    def render(b: dict) -> str:
        n = sum(1 for d in b["drones"] if d["state"] == "airborne")
        return f"{b['clock']} {len(b['drones'])} drones, {n} airborne"

    _get("/fleet", text, render)


@app.command()
def city(
    place: Annotated[str | None, typer.Option("--place")] = None,
    text: Text = False,
) -> None:
    """City mode: open incidents by place, who is going, ETA per vehicle (`eta_min`)."""
    _no_read_in_simulate(text)
    try:
        r = _request("GET", "/city")
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise _unreachable(e, text) from e
    etas: dict[str, dict] = {}
    for e in body.get("events") or []:
        if e.get("type") == "city_dispatch":
            p = e["payload"]
            etas.setdefault(p["incident"], {})[p["vehicle"]] = p["eta_min"]
    rows = [
        {**i, "eta_min": etas.get(i["id"], {})}
        for i in body.get("incidents") or []
        if not place or place.lower() in i.get("name", "").lower()
    ]
    out = {
        "ok": True,
        "clock": body.get("clock_label"),
        "incidents": rows,
        "traffic": body.get("traffic") or [],
    }
    _print(out, text, f"{out['clock']} {len(rows)} incidents")


@app.command()
def docks(text: Text = False) -> None:
    """Slots FREE/OCCUPIED/RESERVED/OFFLINE."""
    _get(
        "/docks",
        text,
        lambda b: f"{b['clock']} slots free {b['slots_free']}/{b['slots_total']}",
    )


@app.command()
def pilots(text: Text = False) -> None:
    """Load per pilot (n/4)."""
    _get(
        "/pilots",
        text,
        lambda b: " ".join(f"{p['id']} {p['load']}" for p in b["pilots"]),
    )


@app.command()
def incidents(
    id_: Annotated[str | None, typer.Option("--id")] = None,
    text: Text = False,
) -> None:
    """Open incidents, geofence, drones, units, authorisations, feed facts so far."""
    if id_:
        _get(
            f"/incidents/{id_}",
            text,
            lambda b: "{id} {kind} {status} P{priority}".format(**b["incident"]),
        )
        return

    def render(b: dict) -> str:
        items = [f"{i['id']} {i['kind']} {i['status']}" for i in b["incidents"]]
        return f"{b['clock']} " + ("; ".join(items) or "no incidents")

    _get("/incidents", text, render)


@app.command()
def airspace(text: Text = False) -> None:
    """Zones (active?), weather per sector."""
    _get(
        "/airspace",
        text,
        lambda b: f"{b['clock']} {len(b['zones'])} zones, grounded {b['grounded'] or 'none'}",
    )


@app.command()
def units(text: Text = False) -> None:
    """Ground units, status, ETA (simulated)."""
    _get("/units", text, _count_line("units"))


@app.command()
def events(since: Since = None, text: Text = False) -> None:
    """Events since a sim time."""
    _get("/events", text, _count_line("events"), {"since": since})


@app.command()
def approvals(
    status_: Annotated[str | None, typer.Option("--status")] = None,
    text: Text = False,
) -> None:
    """Approvals, optionally by status."""
    _get("/approvals", text, _count_line("approvals"), {"status": status_})


@app.command("privacy-log")
def privacy_log(since: Since = None, text: Text = False) -> None:
    """The append-only privacy log."""
    _get("/privacy-log", text, _count_line("privacy_log"), {"since": since})


@app.command()
def kpi(text: Text = False) -> None:
    """Scorecard data (+ baseline if run)."""

    def render(b: dict) -> str:
        m = b["managed"]
        return f"{b['clock']} coverage {m['coverage_pct']}% flights {m['flights']}"

    _get("/kpi", text, render)


@app.command()
def decisions(text: Text = False) -> None:
    """Sim decision log (for the shift report)."""
    _get("/decisions", text, _count_line("decisions"))


@app.command()
def feed(incident: Incident, text: Text = False) -> None:
    """Structured facts from drones on station (no identities)."""
    _no_read_in_simulate(text)
    try:
        r = _request("GET", f"/incidents/{incident}")
        body = r.json()
    except TRANSPORT_ERRORS as e:
        raise _unreachable(e, text) from None
    if r.status_code == 200 and isinstance(body, dict) and body.get("incident"):
        facts = body["incident"]["facts"]
        out = {"ok": True, "simulated": True, "clock": body.get("clock")}
        out |= {"incident": incident, "facts": facts}
        line = f"{incident}: {len(facts)} facts" + (
            f", latest {facts[-1]['fact']}" if facts else ""
        )
        _print(out, text, line)
        return
    if _is_rejection(r.status_code, body):
        raise _reject(body, text)
    raise _fail(f"HTTP {r.status_code}", text)


@plan_app.command("rotation")
def plan_rotation(
    loop: Annotated[str | None, typer.Option("--loop")] = None,
    text: Text = False,
) -> None:
    """The sim's next reliefs (read-only)."""

    def render(b: dict) -> str:
        return " | ".join(
            f"{p['loop']}: {p['next_relief']} in {p['minutes_to_relief']} min" for p in b["plans"]
        )

    _get("/plan/rotation", text, render, {"loop": loop})


@route_app.command("check")
def route_check(
    drone: Drone,
    to: Annotated[str, typer.Option("--to")],
    text: Text = False,
) -> None:
    """Zones crossed, recording on/off, ETA."""

    def render(b: dict) -> str:
        zones = ", ".join(z["id"] for z in b["zones"]) or "clear"
        return f"{b['drone']} zones: {zones}; recording {b['recording']}; ETA {b['eta']}"

    _get("/route/check", text, render, {"drone": drone, "to": to})


@track_app.command("status")
def track_status(incident: Incident, text: Text = False) -> None:
    """Tracker, battery, next relief, handoff ETA."""

    def render(b: dict) -> str:
        return (
            f"{b['incident']} tracker {b['tracker']} ({b['battery']}%), "
            f"next relief {b['next_relief']}, handoff ETA {b['handoff_eta_min']} min"
        )

    _get("/track/status", text, render, {"incident": incident})


@relay_app.command("plan")
def relay_plan(incident: Incident, text: Text = False) -> None:
    """Read-only projection."""

    def render(b: dict) -> str:
        return (
            f"{b['incident']} tracker {b['tracker']}, relief {b['relief_candidate']} "
            f"at {b['relief_at_pct']}% (in {b['minutes_to_relief']} min)"
        )

    _get("/relay/plan", text, render, {"incident": incident})


# ---------------------------------------------------------------- dispatch
@incident_app.command("open")
def incident_open(
    from_alert: Annotated[str, typer.Option("--from-alert")],
    text: Text = False,
) -> None:
    """Open an incident from a CAD alert."""
    _write("open_incident", {"from_alert": from_alert}, text)


@incident_app.command("close")
def incident_close(
    id_: Annotated[str, typer.Option("--id")],
    note: Annotated[str, typer.Option("--note")],
    text: Text = False,
) -> None:
    """Close an incident."""
    _write("close_incident", {"incident": id_, "note": note}, text)


@app.command()
def launch(
    drone: Drone,
    mission: Annotated[str, typer.Option("--mission")],
    incident: Incident,
    to: Annotated[str, typer.Option("--to")],
    why: Annotated[str | None, typer.Option("--why")] = None,
    pilot: Pilot = None,
    basis: Annotated[str | None, typer.Option("--basis", help="default: --why")] = None,
    for_min: Annotated[
        float | None, typer.Option("--for-min", help="minutes on task, for the reserve check")
    ] = None,
    text: Text = False,
) -> None:
    """Launch a drone (incident or perimeter). Every launch states its basis."""
    args = {
        "drone": drone,
        "mission": mission,
        "incident": incident,
        "to": to,
        "why": why,
        "basis": basis or why,
        "pilot": pilot,
        "for_min": for_min,
    }
    _write("launch", args, text)


@app.command()
def divert(
    drone: Drone,
    incident: Incident,
    mission: Annotated[str, typer.Option("--mission")] = "track",
    pilot: Pilot = None,
    why: Why = None,
    text: Text = False,
) -> None:
    """Retask an airborne drone. Track needs an authorisation."""
    args = {
        "drone": drone,
        "incident": incident,
        "mission": mission,
        "pilot": pilot,
        "why": why,
    }
    _write("divert", args, text)


@track_app.command("assign")
def track_assign(drone: Drone, incident: Incident, text: Text = False) -> None:
    """Put an airborne drone on the authorised track."""
    _write("assign_track", {"drone": drone, "incident": incident}, text)


@propose_app.command("standing-policy")
def propose_standing_policy(
    scope: Annotated[str, typer.Option("--scope")],
    text: Text = False,
) -> None:
    """Card: the standing auto-launch policy."""
    _propose("standing_policy", {"scope": scope}, scope, text)


@propose_app.command("track")
def propose_track(
    incident: Incident,
    scope: Annotated[str, typer.Option("--scope")],
    text: Text = False,
) -> None:
    """Card: track a vehicle, with scope and expiry."""
    _propose("track", {"incident": incident, "scope": scope}, f"track {incident}: {scope}", text)


@propose_app.command("broadcast", cls=_MessageCommand)
def propose_broadcast(
    drone: Drone,
    text_: Annotated[str, typer.Option("--text", help="what the loudspeaker says")],
    one_line: OneLine = False,
) -> None:
    """Card: loudspeaker message (first --text is the message, a second one is the switch)."""
    _propose("broadcast", {"drone": drone, "text": text_}, f"{drone}: '{text_}'", one_line)


# ---------------------------------------------------------------- fleet
@app.command()
def rtb(
    drone: Drone,
    dock: Annotated[str | None, typer.Option("--dock")] = None,
    why: Why = None,
    text: Text = False,
) -> None:
    """Return to base; slot booked first."""
    _write("rtb", {"drone": drone, "dock": dock, "why": why}, text)


@propose_app.command("out-of-service")
def propose_out_of_service(
    drone: Drone,
    why: Annotated[str, typer.Option("--why")],
    text: Text = False,
) -> None:
    """Card: take a drone out of service."""
    _propose("out_of_service", {"drone": drone, "why": why}, f"{drone}: {why}", text)


# ---------------------------------------------------------------- airspace
@app.command()
def hold(
    drone: Drone,
    at_boundary: Annotated[str, typer.Option("--at-boundary")],
    text: Text = False,
) -> None:
    """Hold at a zone boundary."""
    _write("hold", {"drone": drone, "at_boundary": at_boundary}, text)


@app.command()
def corridor(
    drone: Drone,
    incident: Incident,
    to: Annotated[str, typer.Option("--to")],
    why: Why = None,
    text: Text = False,
) -> None:
    """Corridor watch on a legal route."""
    _write("corridor_watch", {"drone": drone, "incident": incident, "to": to, "why": why}, text)


@propose_app.command("zone")
def propose_zone(
    id_: Annotated[str, typer.Option("--id")],
    until: Annotated[str, typer.Option("--until")],
    text: Text = False,
) -> None:
    """Card: activate a temporary zone."""
    _propose("zone", {"zone": id_, "until": until}, f"activate {id_} until {until}", text)


# ---------------------------------------------------------------- intel
@evidence_app.command("tag")
def evidence_tag(
    incident: Incident,
    clip: Annotated[str, typer.Option("--clip")],
    what: Annotated[str, typer.Option("--what")],
    text: Text = False,
) -> None:
    """Tag a clip."""
    _write("evidence_tag", {"incident": incident, "clip": clip, "what": what}, text)


# ---------------------------------------------------------------- privacy
@app.command("set-recording")
def set_recording(
    drone: Drone,
    on: Annotated[bool, typer.Option("--on/--off", help="on is refused outside a geofence")],
    text: Text = False,
) -> None:
    """Override recording for a drone."""
    _write("set_recording", {"drone": drone, "on": on}, text)


@app.command("release-footage")
def release_footage(
    approval: Annotated[str, typer.Option("--approval")],
    text: Text = False,
) -> None:
    """Release a clip whose footage_release card was approved."""
    _write("release_footage", {"approval_id": approval}, text)


@propose_app.command("footage-release")
def propose_footage_release(
    incident: Incident,
    clip: Annotated[str, typer.Option("--clip")],
    to: Annotated[str, typer.Option("--to")],
    text: Text = False,
) -> None:
    """Card: release a clip."""
    args = {"incident": incident, "clip": clip, "to": to}
    _propose("footage_release", args, f"release {clip} of {incident} to {to}", text)


@privacy_app.command("check")
def privacy_check(
    request: Annotated[str, typer.Option("--request")],
    text: Text = False,
) -> None:
    """Classify a request; face ID is always PRIVACY_POLICY."""
    _write("privacy_check", {"request": request}, text)


# ---------------------------------------------------------------- comms
@unit_app.command("task")
def unit_task(
    unit: Annotated[str, typer.Option("--unit")],
    incident: Incident,
    text: Text = False,
) -> None:
    """Task a ground unit."""
    _write("task_unit", {"unit": unit, "incident": incident}, text)


@unit_app.command("release")
def unit_release(
    unit: Annotated[str, typer.Option("--unit")],
    incident: Incident,
    evidence: Annotated[str, typer.Option("--evidence")],
    note: Annotated[str | None, typer.Option("--note", help="plain-words reason")] = None,
    text: Text = False,
) -> None:
    """Early release; evidence must quote a revealed feed fact."""
    args = {"unit": unit, "incident": incident, "evidence": evidence, "note": note}
    _write("release_unit", args, text)


@app.command(cls=_MessageCommand)
def advisory(
    area: Annotated[str, typer.Option("--area")],
    text_: Annotated[str, typer.Option("--text", help="the advisory text")],
    until: Annotated[str, typer.Option("--until")],
    one_line: OneLine = False,
) -> None:
    """Public advisory (first --text is the advisory, a second one is the switch)."""
    _write("issue_advisory", {"area": area, "text": text_, "until": until}, one_line)


# ---------------------------------------------------------------- any agent
@app.command("log-refusal")
def log_refusal(
    about: Annotated[str, typer.Option("--about")],
    code: Annotated[str, typer.Option("--code")],
    by: Annotated[str | None, typer.Option("--by")] = None,
    drone: Annotated[str | None, typer.Option("--drone")] = None,
    pilot: Annotated[str | None, typer.Option("--pilot")] = None,
    fallback_pilot: Annotated[str | None, typer.Option("--fallback-pilot")] = None,
    text: Text = False,
) -> None:
    """Log a refusal with its §4.2 code."""
    args = {
        "about": about,
        "code": code,
        "by": by,
        "drone": drone,
        "pilot": pilot,
        "fallback_pilot": fallback_pilot,
    }
    _write("log_refusal", args, text)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def simulate(ctx: typer.Context) -> None:
    """What-if: same args as a write. Result + KPI delta; changes nothing."""
    if not ctx.args:
        raise _fail("usage: airgrid simulate <same args as a write>", False)
    if ctx.args[0] == "simulate":
        raise _fail("cannot simulate simulate", False)
    token = _simulating.set(True)
    try:
        code = typer.main.get_command(app).main(args=ctx.args, standalone_mode=False)
    finally:
        _simulating.reset(token)
    raise typer.Exit(code or 0)


# ---------------------------------------------------------------- human-only (World Bot)
@app.command()
def approve(approval_id: str, text: Text = False) -> None:
    """Human token only."""
    _call("POST", f"/approvals/{approval_id}/approve", text, json={})


@app.command()
def reject(approval_id: str, reason: str, text: Text = False) -> None:
    """Human token only."""
    _call("POST", f"/approvals/{approval_id}/reject", text, json={"reason": reason})


@app.command("take-control")
def take_control(drone: str, text: Text = False) -> None:
    """Human token only (remote_pilot)."""
    _write("take_control", {"drone": drone}, text)


@app.command("release-control")
def release_control(drone: str, text: Text = False) -> None:
    """Human token only (remote_pilot)."""
    _write("release_control", {"drone": drone}, text)


if __name__ == "__main__":
    app()
