# Preset Animation Library

Status as of 2026-09-02: **Built, opt-in via `params.animate`** (implies `params.rig`). Bakes
two named clips — `Idle` and `Walk` — onto a rigged character and embeds them in a new
`animated.glb` output, alongside the existing static `rigged.glb`.

**Honest caveat up front:** real animated output is currently inconsistent across
generations. Both real SkinTokens rigs available when this was built failed the validation
step below, for a reason that's a bug in an upstream feature, not in this code (see "The
Mixamo template bug" below). The framework itself is verified correct — see "Verifying without
a passing real rig" — it just needs either a generation whose predicted skeleton happens to
come out in canonical part order, or an improvement to SkinTokens' own template mapping, to
produce animated output in practice today.

## Why this needs a bone-naming template at all

SkinTokens (see [docs/RIG.md](./RIG.md)) predicts a skeleton per-mesh — bone count and
hierarchy shape vary by subject, and by default bones are just named `bone_0`, `bone_1`, ...
Hand-authoring an animation against arbitrary per-mesh bone indices isn't reusable across
generations. `SkinTokenRigTrimesh`'s `skeleton_template` param can rename the predicted
skeleton onto a fixed convention instead — `"Mixamo"` was the obvious choice: a well-known,
fully-specified biped naming (`mixamorig:Hips`, `mixamorig:LeftUpLeg`, etc.) that, if the
renaming is correct, is stable across every generation regardless of the source mesh.

## The Mixamo template bug

`skeleton_template="Mixamo"` does **not** do real anatomical matching. Traced through the
vendored source (`skeleton_template.py`): it tries a geometric heuristic
(`_apply_humanoid_template`, using subtree size/PCA-based left-right detection) first, but
when that heuristic can't confidently match, it silently falls back to `apply_joint_name_template`
— which just overlays a fixed template name list onto the skeleton's bones **in their existing
traversal-order index**, with no verification that the result is hierarchically sensible.

Verified empirically against both real rigged outputs on hand (extracted their actual bone
positions/parents and ran `apply_asset_joint_name_template` directly, bypassing the expensive
GPU re-run — see git history for the throwaway test scripts): the first ~10 bones (Hips →
Spine → Spine1 → Spine2 → Neck → Head → LeftShoulder → LeftArm → LeftForeArm → LeftHand) came
out **correct**, because that prefix happens to match the template's assumed order. Bone index
10 onward did not — e.g. `mixamorig:RightShoulder`'s *renamed* parent came back as
`mixamorig:LeftHand`, because bone 10 in the real skeleton is actually where the first hand's
finger sub-chains begin (this particular generation's predicted topology puts one arm's
fingers before the second arm starts), not a mirrored second shoulder as the flat template
list assumes at that position.

This is why animation baking **validates the renamed skeleton before trusting it** rather than
assuming `skeleton_template="Mixamo"` worked:

```
validate_skeleton() in workers/blender/animate.py checks that every parent-child edge the
two animations actually depend on holds — e.g. that "mixamorig:LeftForeArm"'s real parent is
"mixamorig:LeftArm", not something the flat overlay happened to leave there. If any required
edge is wrong or missing, animation baking is skipped entirely: the job still succeeds, it
just has no animated output.
```

Toe bones are deliberately excluded from validation — a walk cycle reads fine without
animating them, and requiring them would reject otherwise-animatable skeletons for no reason.

## A second, unrelated bug: multi-action glTF export corruption

Blender 5.2 introduced a new "Animation Layers" / slotted-action data model. Baking `Idle` and
`Walk` as two separate `bpy.data.actions` and exporting both directly via
`export_animation_mode="ACTIONS"` **corrupts every action but the first** when they key
different (non-identical) subsets of bones — verified empirically: a lone action re-imports
with correct, real varying keyframe values; adding a second action that touches different
bones causes the *second* action's keyed channels to come back flattened to the rest pose
after export/re-import, even though the in-memory keyframes were correct right up until
export.

Fix: push each action onto its own NLA track (`animation_data.nla_tracks.new()` +
`track.strips.new(name, 1, action)`) and export with `export_animation_mode="NLA_TRACKS"`
instead. Verified this produces correct, independent, real-varying-value clips for both
actions. The exported glTF animation's name comes from the **NLA track's** name in this mode,
which is why `push_to_nla()` names the track after the action rather than leaving Blender's
default track name.

This looks like a genuine Blender 5.2 glTF-exporter bug in how it isolates multiple actions
under the newer layered-action system, not a mistake in the keyframing itself — worth
re-checking against future Blender releases in case it gets fixed upstream.

## Verifying without a passing real rig

Since neither real rig on hand passes `validate_skeleton()`, correctness of the
baking/export/loop logic itself was verified against a **hand-built synthetic skeleton**: a
minimal biped armature with the exact Mixamo bone names and hierarchy `validate_skeleton()`
expects, in a sane T-pose bind. Running `animate.py` against it: validation passes, both
clips bake, export succeeds, and the re-imported GLB's raw F-curve data shows correct,
smoothly-varying rotation values across the full loop for every keyed bone (confirmed via
direct F-curve/keyframe inspection, not frame-by-frame playback — Blender 5.2's new
layered-action API needs an explicit slot re-bind for `frame_set()`-based playback to work on
a freshly re-imported file, which is a test-harness quirk, not a data problem).

This proves the framework — validation, keyframing, NLA-track export, clip naming — works
correctly end to end. What it doesn't prove is a high hit rate on real generations; that's
gated entirely on SkinTokens' own Mixamo-template mapping producing a canonical result, which
today it usually doesn't for anything but the simplest topologies.

## The two clips

Both are intentionally simple, procedurally keyframed (not sourced mocap — see
[README.md](../README.md)'s "Compared to Meshy 7" for why): rotation-only, no finger/toe
animation, a small set of bones (spine, head, arms, legs).

- **Idle** — a slow 2s breathing/sway loop (`Spine2`, `Head`, `LeftArm`/`RightArm`).
- **Walk** — a stylized 1s walk cycle: alternating leg swing (`*UpLeg`), knee bend synced to
  swing phase (`*Leg`), and opposite-side arm counter-swing (`*Arm`).

Both loop seamlessly (each action's closing frame duplicates its opening frame's pose).
Rotations only, no root-bone location/bob — keeps the result scale-invariant regardless of
`params.scale`, at the cost of a flatter-looking walk than one with real hip bob.

## Still open

- No real generation on hand actually produces a canonical-enough skeleton to animate — see
  "Honest caveat" above. Revisit once more generations have been run with
  `skeleton_template="Mixamo"`, or if the upstream template-matching heuristic improves.
- No fallback to a non-Mixamo naming convention (e.g. animating directly against whatever
  bone hierarchy SkinTokens predicts, using our own position-based role detection instead of
  trusting the upstream template). That would likely have a much higher real-world hit rate,
  but is substantially more work — essentially redoing part of what SkinTokens' own template
  matching attempts, correctly this time. Deliberately deferred in favor of shipping the
  simpler validate-or-skip approach first.
- No retargeting of external mocap — these two clips are hand-authored from scratch, not
  sourced from Mixamo's own animation library (which would need bone names to match exactly,
  and still requires the validation above to hold).
