"""Simulated duty-pilot, CAD and radio lines. Templates only, no LLM, always labelled simulated."""

from __future__ import annotations

DUTY_PILOT = {
    "rotation": "{drone} is mine for {loop}. Lifting now.",
    "launch": "{drone} accepted, tasking {incident}.",
}
RADIO = {
    "unit_tasked": "Copy, en route to {incident}.",
    "unit_on_scene": "On scene at {incident}.",
    "unit_released": "Released from {incident}, back on patrol.",
    "unit_cleared": "Clear of {incident}, returning.",
}


def duty_pilot_name(raw_name: str) -> str:
    """'Asha Menon (duty pilot, simulated)' → 'Asha Menon'."""
    return raw_name.split("(")[0].strip()


def duty_pilot(pilot_id: str, name: str, line: str) -> str:
    return f'🛩 {pilot_id} {name} (duty pilot, simulated): "{line}"'


def radio(unit: str, line: str) -> str:
    return f'📻 {unit} (simulated): "{line}"'


def simulated_lines(event: dict, pilots: dict[str, str]) -> list[str]:
    """Lines an event voices. `pilots`: simulated pilot id → display name."""
    kind, p = event.get("type"), event.get("payload") or {}
    if kind in DUTY_PILOT and p.get("pilot") in pilots:
        text = DUTY_PILOT[kind].format(
            drone=p.get("drone"), loop=p.get("loop"), incident=p.get("incident")
        )
        return [duty_pilot(p["pilot"], pilots[p["pilot"]], text)]
    if kind in RADIO and p.get("unit"):
        return [radio(p["unit"], RADIO[kind].format(incident=p.get("incident")))]
    if kind == "unit_radio" and p.get("unit"):
        return [radio(p["unit"], str(p.get("text") or p.get("note") or "radio check"))]
    if kind == "cad_alert" and p.get("report"):
        return [radio("CAD caller", p["report"])]
    return []
