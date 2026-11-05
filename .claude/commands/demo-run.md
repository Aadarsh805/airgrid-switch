---
description: Dry-run Friday Rush end to end with the FakeRoom
---
Load FRIDAY-RUSH in demo mode with the World Bot on `--fake-room`. Play kazum from the golden actions in `scenarios/friday_rush.yaml` (approve the standing policy, loudspeaker, track with scope; as FO push face recognition, the airfield route, and "keep it up just in case"; take control of D-04). Don't play the runtime agents; use golden narration stubs unless Phase 06 is done, then run the real agents headless.
Print: event timeline, every approval with its decision, every refusal with its code, simulated duty-pilot/radio lines, final KPIs vs baseline, any moment the clock didn't hold on a pending approval, any line that differs from `docs/golden-timeline.txt`. Save to `runs/demo-dryrun-<timestamp>.md`.
