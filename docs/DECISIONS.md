# Open Decisions

Tracking §11 of the master plan. Settled decisions move to the top with a date and rationale.

## Settled

### GPU / VRAM — settled 2026-08-22 by measurement
RTX 2070, 8 GB, compute capability 7.5 (Turing). See [hardware.md](./hardware.md).
Consequence: bottom tier of the plan's model table. TRELLIS 2 and Hunyuan3D are out;
InstantMesh / TripoSR are in. The Turing compute-capability floor is the binding constraint,
not the VRAM number.

## Still open

- **OS — WSL2 vs native Linux vs native Windows.** WSL2 is not currently installed. The plan
  is firm that native Windows costs days on CUDA-extension builds.
- **Orchestrator language — TS vs Python.** Plan recommends TS (one mental model, and the UI
  is the part that gets iterated most). Harrison's day job is React/TypeScript, which reinforces this.
- **Primary target — Godot 4 vs Unreal.** Harrison ships both (Homestead Defense is Godot 4
  low-poly; Homestead & Market / Elixir of Life are Unreal). This sets default tri budget and
  texture resolution. Low-poly Godot targets are also the kinder ask of an 8 GB card.
- **Rigging in v1 or v2.** Cutting it makes Phase 2 ~40% shorter. Props, crops, and terrain —
  which is most of what the Godot game needs — don't need it at all.
