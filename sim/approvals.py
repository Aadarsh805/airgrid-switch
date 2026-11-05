"""Approvals (agents propose, humans decide) and authorisations (scope + expiry).

Approval ids A-0001… are sequential per run. Approving has the effect named in
docs/phase03-interface.md; expiries run in the tick's expiries step.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from sim.geo import EPS, fmt, tmin

TYPES = (
    "track",
    "broadcast",
    "footage_release",
    "zone",
    "divert_off_incident",
    "out_of_service",
    "standing_policy",
)
HOLD_TYPES = TYPES  # every pending human decision holds the demo/live clock
WHAT = {"broadcast": "loudspeaker"}  # timeline wording per approval type
SILENT = {"standing_policy"}  # approved without a timeline line


@dataclass
class Approval:
    id: str
    type: str
    created_by: str
    args: dict
    summary: str
    hold_clock: bool
    created_min: float
    status: str = "pending"
    decided_by: str | None = None
    reason: str | None = None
    decided_min: float | None = None
    used: bool = False

    def view(self) -> dict:
        v = asdict(self)
        v["created"] = fmt(self.created_min)
        v["decided"] = fmt(self.decided_min) if self.decided_min is not None else None
        return v


@dataclass
class Authorisation:
    id: str
    approval_id: str
    incident: str | None
    kind: str
    scope: str | None
    approved_by: str
    approved_min: float
    expires_min: float | None
    status: str = "active"
    meta: dict = field(default_factory=dict)

    def view(self) -> dict:
        v = asdict(self)
        v["expires"] = fmt(self.expires_min) if self.expires_min is not None else None
        return v


def track_minutes(args: dict) -> float | None:
    if args.get("minutes") is not None:
        return float(args["minutes"])
    m = re.search(r"(\d+(?:\.\d+)?)\s*min", str(args.get("scope") or ""))
    return float(m.group(1)) if m else None


class ApprovalBook:
    def __init__(self):
        self.items: dict[str, Approval] = {}
        self.auths: list[Authorisation] = []

    # --- proposals ---
    def propose(
        self,
        t: float,
        created_by: str,
        type_: str,
        args: dict,
        summary: str,
        hold_clock: bool | None = None,
    ) -> Approval:
        """`hold_clock` None (every caller outside tests) means the type decides."""
        if hold_clock is None:
            hold_clock = type_ in HOLD_TYPES
        aid = f"A-{len(self.items) + 1:04d}"
        a = Approval(aid, type_, created_by, dict(args), summary, bool(hold_clock), t)
        self.items[aid] = a
        return a

    def pending(self) -> list[Approval]:
        return [a for a in self.items.values() if a.status == "pending"]

    def by_status(self, status: str | None) -> list[Approval]:
        return [a for a in self.items.values() if status is None or a.status == status]

    def approved(self, type_: str, **match) -> list[Approval]:
        return [
            a
            for a in self.items.values()
            if a.type == type_
            and a.status == "approved"
            and all(a.args.get(k) == v for k, v in match.items())
        ]

    def consume(self, type_: str, **match) -> tuple[Approval | None, str | None]:
        """Use one approved, unused approval of that type (marks it `used`).
        Returns (approval, None) or (None, id of an already-used match)."""
        cands = self.approved(type_, **match)
        fresh = [a for a in cands if not a.used]
        if fresh:
            fresh[0].used = True
            return fresh[0], None
        return None, (cands[-1].id if cands else None)

    # --- authorisations ---
    def add_auth(self, a: Approval, t: float, by: str, incident, kind, scope, expires):
        au = Authorisation(
            f"AUTH-{len(self.auths) + 1:04d}", a.id, incident, kind, scope, by, t, expires
        )
        self.auths.append(au)
        return au

    def auth(self, incident: str, kind: str = "track") -> Authorisation | None:
        """Latest authorisation of that kind for the incident (any status)."""
        found = [x for x in self.auths if x.incident == incident and x.kind == kind]
        return found[-1] if found else None

    def active_auth(self, incident: str, kind: str = "track") -> Authorisation | None:
        au = self.auth(incident, kind)
        return au if au and au.status == "active" else None

    def expire(self, t: float) -> list[Authorisation]:
        out = []
        for au in self.auths:
            if au.status == "active" and au.expires_min is not None and au.expires_min <= t + EPS:
                au.status = "expired"
                out.append(au)
        return out


def zone_until(args: dict) -> float | None:
    return tmin(args["until"]) if args.get("until") else None
