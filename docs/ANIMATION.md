# Preset Animation Library

Status as of 2026-09-02: **Built, opt-in via `params.animate`** (implies `params.rig`). Bakes
two named clips — `Idle` and `Walk` — onto a rigged character and embeds them in a new
`animated.glb` output, alongside the existing static `rigged.glb`.

**Real hit rate fixed.** The first version of this depended on SkinTokens' `skeleton_template
="Mixamo"` bone-naming feature, which turned out to be broken (see "The Mixamo template bug"
below) — both real test rigs on hand failed validation 100% of the time. `animate.py` now
classifies the skeleton directly from its own bone *topology* instead of trusting any naming
convention, and correctly identifies every needed bone on the same real rig that used to fail
100% of the time (see "Topology-based classification, not naming" below). `skeleton_template`
is no longer requested at all for animation purposes.

## Why this needs *some* way to identify bone roles

SkinTokens (see [docs/RIG.md](./RIG.md)) predicts a skeleton per-mesh — bone count and
hierarchy shape vary by subject, and by default bones are just named `bone_0`, `bone_1`, ...
Hand-authoring an animation needs *some* reliable way to say "rotate the left upper arm" that
holds across different generations, not just one specific mesh's arbitrary bone indices.

## The Mixamo template bug (why naming wasn't the answer)

The first attempt requested `SkinTokenRigTrimesh`'s `skeleton_template="Mixamo"` param, which
renames the predicted skeleton onto Mixamo's fixed convention (`mixamorig:Hips`,
`mixamorig:LeftUpLeg`, etc.). It does **not** do real anatomical matching, though. Traced
through the vendored source (`skeleton_template.py`): it tries a geometric heuristic
(`_apply_humanoid_template`, using subtree size/PCA-based left-right detection) first, but
when that heuristic can't confidently match, it silently falls back to
`apply_joint_name_template` — which just overlays a fixed template name list onto the
skeleton's bones **in their existing traversal-order index**, with no verification that the
result is hierarchically sensible.

Verified empirically against both real rigged outputs on hand (extracted their actual bone
positions/parents and ran `apply_asset_joint_name_template` directly, bypassing the expensive
GPU re-run): the first ~10 bones (Hips → Spine → Spine1 → Spine2 → Neck → Head →
LeftShoulder → LeftArm → LeftForeArm → LeftHand) came out **correct**, because that prefix
happens to match the template's assumed order. Bone index 10 onward did not — e.g.
`mixamorig:RightShoulder`'s *renamed* parent came back as `mixamorig:LeftHand`, because bone
10 in the real skeleton is actually where the first hand's finger sub-chains begin (this
generation's predicted topology puts one arm's fingers before the second arm starts), not a
mirrored second shoulder as the flat template list assumes at that position.

## Topology-based classification, not naming

The bone *topology* SkinTokens predicts, unlike its naming, was independently validated as
reliably correct (docs/RIG.md's quality test: "34 bones in exactly the topology you'd want").
`classify_skeleton()` in `workers/blender/animate.py` reconstructs the same semantic roles
(hips, spine, head, two arms, two legs) purely from parent/child structure — no naming
convention required at all, so it works equally well on the default "Keep model names" output
(`bone_0`, `bone_1`, ...) that the rig worker already produces without any special param.

The expected shape, and how each role is picked:

```
hips (root, the one bone with no parent)
+-- spine chain (bones with exactly one child each) ending at a branch point with
|   exactly 3 children:
|     +-- head/neck chain: the SMALLEST of the three subtrees
|     +-- arm chain A: bigger than the head chain (shoulder/upper/fore/hand, plus
|     |   fingers when SkinTokens predicts them)
|     +-- arm chain B: mirrors A
+-- leg chain A (bones with exactly one child each, direct child of hips)
+-- leg chain B: mirrors A (identified because the spine branch's subtree is far
    bigger than either leg's -- it goes on to include the head and both arms)
```

Head vs. arms is decided by subtree **size**, not by whether a branch forks into fingers —
that detail mattered during development: an early version used "does this branch ever fork
again" to spot the head (arms fork into fingers, head doesn't), which correctly handled real
SkinTokens output but failed on a hand-built synthetic test skeleton that had no finger bones
at all (all three branches looked equally "non-forking" to that check). Switching to
size-based comparison (a full arm is reliably bigger than a neck+head chain, whether or not it
has fingers) fixed the synthetic case without regressing the real one — verified both still
pass after the change.

Left/right (or which of the mirrored pair is "arm A" vs "arm B") is never determined at all,
deliberately — the two preset animations are symmetric (legs alternate, arms counter-swing),
so which physical side gets internally labeled "A" doesn't change how the result looks.
Skipping that determination removes a whole class of potential bugs for zero visual cost.

Toe and finger bones are deliberately excluded from what's required — a walk cycle reads fine
without animating them, and requiring them would reject otherwise-animatable skeletons for no
reason.

Anything that doesn't match the expected shape (non-bipeds, degenerate rigs, an unexpected
number of children somewhere) is declined cleanly: `classify_skeleton()` returns `None`,
`animate.py` exits having baked nothing, and the job still succeeds without an animated
output — same fail-soft philosophy as before, just with a much smaller failure surface now.

## Verified against the real rig that used to fail 100% of the time

Ran `animate.py` directly against `scratch/.../rig/rigged.glb` (the same real "Keep model
names" rigged output that failed every check under the old Mixamo-name validation):
`classify_skeleton()` correctly identified all 6 bones the animations touch (2 arms, 2×2 leg
segments), confirmed by inspecting the exported clip's raw F-curve data — every expected bone
carries a real, smoothly-varying rotation curve matching the keyframed poses, and no
unexpected bone does. Rendered the `Walk` clip on this real mesh and confirmed the limbs
visibly alternate in the correct symmetric pattern frame to frame (the character's own rest
orientation in this particular test asset happens to be lying flat rather than standing, which
made framing a flattering screenshot annoying but is unrelated to the animation logic itself).

Also re-verified against the hand-built synthetic skeleton used to validate the original
framework (see below) and a non-rigged mesh (correctly declines, no armature present) — both
still behave correctly after the size-based head/arm fix.

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

## The synthetic test skeleton

Before a real rig was available to validate against, framework correctness (keyframing,
NLA-track export, clip naming) was verified against a hand-built synthetic biped: a minimal
armature with Mixamo bone names and hierarchy, in a sane T-pose bind. This was useful for
proving the *mechanics* worked end to end while the naming approach was still being debugged,
but — as noted above — it also exposed a real gap in the head/arm classification logic (no
finger bones to distinguish arms from head by forking), which the size-based fix resolved.
Kept in the test suite as a regression check now that a real rig is also available.

## The two clips

Both are intentionally simple, procedurally keyframed (not sourced mocap — see
[README.md](../README.md)'s "Compared to Meshy 7" for why): rotation-only, no finger/toe
animation, a small set of bones (spine, head, arms, legs) identified by role, not by name.

- **Idle** — a slow 2s breathing/sway loop (spine-bend bone, head, both arms).
- **Walk** — a stylized 1s walk cycle: alternating leg swing (both "up leg" bones), knee bend
  synced to swing phase (both "knee" bones, when a leg chain has one), and opposite-side arm
  counter-swing.

Both loop seamlessly (each action's closing frame duplicates its opening frame's pose).
Rotations only, no root-bone location/bob — keeps the result scale-invariant regardless of
`params.scale`, at the cost of a flatter-looking walk than one with real hip bob.

## Still open

- Rotation axis convention (local X = forward/back swing) is still an assumption, not
  something computed per-bone from actual bone roll. It happened to look correct on the one
  real rig verified above (limbs visibly alternate in a sane symmetric pattern), but a
  different generation's bone roll convention could in principle produce a stiffer or
  sideways-looking swing. Revisit if that turns out to matter across more real generations.
- No retargeting of external mocap — these two clips are hand-authored from scratch, not
  sourced from Mixamo's own animation library.
- Only tested against one real rigged mesh (a humanoid robot) plus one hand-built synthetic
  skeleton. Broader coverage (more subjects, quadrupeds correctly declining, etc.) would
  increase confidence but hasn't been run yet.
