# Switch room setup (paste, don't import)

- `rooms/<room>.txt`: Instructions for each room, the verbatim Rules from `docs/05-AGENTS.md`.
  Paste into Switch Console, room Configuration, Instructions. A test keeps them equal to the doc.
- `agent-rooms.yaml`: which agent joins which room.

Switch templates exist (`room:` YAML with `instructions` and `agents`), but running one creates a
room, and ours already exist through the Discord bridge. So these are plain files. Steps:
`HUMAN_TODO.md`, Phase 08.
