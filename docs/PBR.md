# PBR Materials — Normal Baking + Roughness/Metallic Factors

Status as of 2026-09-02: **Built, opt-in.** `--bake-normal` bakes a real high-poly-to-low-poly
normal map; `--roughness-factor`/`--metallic-factor` set flat PBR factors on every generated
material (whether or not any texture baking happens). Together with the existing base-color +
AO baking (see the "texture baking" work referenced from [README.md](../README.md)), this is
as far as "real" PBR goes without a material-understanding ML model — see "Compared to Meshy
7" below for the honest boundary.

## What's real here, and what isn't

- **Normal map**: real. A full-resolution duplicate of the raw meshgen output is kept
  alongside the working mesh (survives decimation/smoothing/retopology, whatever the final
  low-poly ends up looking like), and a selected-to-active Cycles bake transfers its actual
  surface detail onto the final mesh's UVs. This recovers geometric detail that decimation
  throws away — not a derived/heuristic map.
- **AO map**: real (already existed before this work) — Cycles' own geometry-driven
  occlusion pass.
- **Roughness/metallic**: **not real.** These are flat scalar factors
  (`--roughness-factor`/`--metallic-factor`, default `0.6`/`0.0`), not baked per-pixel maps.
  Getting real per-pixel roughness/metallic needs a model that understands what a surface is
  *made of* (metal vs. plastic vs. cloth), which is out of scope here — see "Compared to Meshy
  7". A sensibly chosen constant (`0.6` roughness reads as a generic matte prop, not
  mirror-glossy or plastic-shiny) is still a real improvement over Blender's opaque default
  (roughness `0.5`, metallic `0`), which was never a deliberate choice.

## Two real bugs found while building the normal bake

Both traced down to the exact same underlying cause pattern this project keeps running into
with Blender's color-management pipeline on sequential bakes — see also the AO-bake
colorspace bug documented in git history from the earlier texture-baking work.

**1. Colorspace tagging corrupts a save, but only when other bakes ran first in the same
session.** Marking a baked image `colorspace_settings.name = "Non-Color"` *before* calling
`.save()` writes a solid-black PNG — but only when this bake follows other bakes (EMIT, AO)
earlier in the same script run. An isolated normal-only bake (no prior bakes in the session)
saves correctly either way, tagged or not. In-memory pixel values read correctly via Python
right up to and immediately after the `.save()` call in both cases — this looks like a stale
color-management cache issue in Blender's file-write path specifically, triggered by mixing
differently-tagged images (sRGB then Non-Color) in one session, not a problem with the baked
data itself.

**Fix**: save first with the image's default colorspace, *then* tag it `Non-Color` afterward
(a metadata-only change that doesn't touch the already-written file, but correctly informs
the Normal Map shader node — and glTF export reading through it — how to interpret the
in-memory image for the rest of the script's run).

**2. Normal bakes don't need color-management encoding at all — verified, not assumed.**
Before finding bug #1, it seemed plausible that a NORMAL bake might need the same encode
treatment as AO (which genuinely does — see the earlier finding that AO's raw linear
light-transport values look wrong without an sRGB encode on save). Verified empirically this
is *not* true for NORMAL bakes: baked the same normal map with default and with `Non-Color`
colorspace, saved each once in isolation, and compared raw pixel values directly — identical
in every case. Blender treats "Normal" (and other data passes) as bypassing the light-transport
color pipeline entirely, unlike AO/EMIT/DIFFUSE, which are real radiometric quantities. This is
why the two bake types need opposite handling: AO must NOT be tagged Non-Color before save;
NORMAL doesn't care about the tag's effect on saved values, but should still be tagged
Non-Color afterward for semantic correctness when read back into a Normal Map node.

## Cage/margin tuning

`cage_extrusion=0.02` and `max_ray_distance=0.05`, calibrated empirically on this project's
test assets (~1-unit scale, per [docs/MESH_GEN.md](./MESH_GEN.md)'s watering-can test case) —
not blind defaults. Too small a cage extrusion and rays miss surface detail sitting outside
the heavily-decimated low-poly's exact shape; too large and rays pick up neighboring geometry
that isn't actually the surface being baked. `max_ray_distance` caps how far a ray travels
looking for the high-poly surface, so it can't pass through thin geometry (a handle, a spout)
and wrongly pick up the far side. Visually verified on the watering-can test mesh: clean
detail capture (ripples, bolt/disc patterns, handle geometry) with no visible black ray-miss
holes at 8000 target tris (this project's default `target_tris`).

## Why bake_normal needs its own opt-in flag, not bundled with bake_texture

Keeping the full-resolution duplicate alive throughout decimation/smoothing/retopology has a
real memory and time cost, and the selected-to-active bake itself is the slowest bake type
this pipeline runs (ray-casting against the full, undecimated mesh — up to 244K tris in this
project's test case). Bundling it into `--bake-texture` by default would tax every textured
job for a feature not every job needs; opt-in matches how `--retopology` and `--bake-texture`
are already each independently gated.

## Compared to Meshy 7

See [README.md](../README.md)'s "Compared to Meshy 7" section for the full comparison. The
relevant boundary: Meshy's 8K "Multiview Diffusion texturing" generates albedo, roughness,
metallic, *and* normal maps from a real material-understanding model trained for exactly this.
This project now has two of those four as real per-pixel maps (albedo via vertex-color
transfer, normal via high-poly bake) and the other two as flat factors. Getting real
roughness/metallic would mean training or running a material-estimation model — a genuinely
different scope of work than anything else in this pipeline, and not attempted.

## Still open

- Roughness/metallic remain flat factors, not maps. No plan to fix this without a
  material-understanding model, which is out of scope for this project's hardware and goals.
- No UV-seam-aware detail smoothing on the normal map — visible seams at UV island borders on
  aggressively-decimated meshes are a known cosmetic artifact, not something this bake
  corrects for.
- `cage_extrusion`/`max_ray_distance` are hardcoded, not exposed as params — calibrated for
  this project's typical ~1-unit asset scale; a very large or very small generated asset might
  need different values. Revisit if that turns out to matter in practice.
