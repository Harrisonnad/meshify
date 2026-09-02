"""Bake a small preset animation library (Idle, Walk) onto a rigged character, by classifying
its skeleton directly from its own bone topology rather than trusting SkinTokens' bone-naming
feature.

The first version of this script requested `skeleton_template="Mixamo"` from the rig worker
(see docs/RIG.md) and validated the *names* it produced before animating. That approach hit a
real ceiling: verified empirically (see docs/ANIMATION.md) that SkinTokens' Mixamo renamer
doesn't do real anatomical matching — it overlays a fixed name list onto bones in their
existing traversal order, which goes wrong past the first ~10 bones whenever a real skeleton's
part order doesn't match the template's assumptions. Both real test rigs on hand failed that
validation 100% of the time, making the feature effectively unusable in practice.

The bone *topology* SkinTokens predicts, unlike its naming, was independently validated as
reliably correct (docs/RIG.md's quality test: "34 bones in exactly the topology you'd want").
classify_skeleton() below reconstructs the same semantic roles (hips, spine, head, two arms,
two legs) purely from parent/child structure — no naming convention required at all, so it
works equally well on the default "Keep model names" output (bone_0, bone_1, ...) and doesn't
depend on skeleton_template being set to anything in particular. Verified against the real
rigged test asset that failed the old name-based validation: classify_skeleton() identifies
every role correctly (see docs/ANIMATION.md for the topology walkthrough).

The expected shape (a standard biped, per SkinTokens' own predicted topology):
  hips (root)
  +-- spine chain (single-child bones) ending at a branch point with exactly 3 children:
      +-- head/neck chain (the smallest of the three subtrees)
      +-- arm chain A (bigger than the head chain -- shoulder/upper/fore/hand, plus
      |   fingers when SkinTokens predicts them, but arms-vs-head is decided purely by
      |   size, not by whether fingers exist, so a simplified rig with no separate
      |   finger bones still classifies correctly)
      +-- arm chain B (mirrors A)
  +-- leg chain A (single-child bones, from hips directly)
  +-- leg chain B (mirrors A)
Anything that doesn't match this shape (non-bipeds, degenerate rigs) is declined cleanly:
this script exits having baked nothing, and the job still succeeds without an animated output.

Run:
  blender --background --python animate.py -- --input rigged.glb --output-dir out/ --name asset
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy

FPS = 24


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to rigged.glb from the rig worker")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--name", required=True)
    return parser.parse_args(argv)


def find_armature():
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    return arms[0] if arms else None


def _children_map(arm) -> dict:
    return {b.name: [c.name for c in b.children] for b in arm.data.bones}


def _subtree_size(name: str, children: dict, memo: dict) -> int:
    if name in memo:
        return memo[name]
    size = 1 + sum(_subtree_size(c, children, memo) for c in children[name])
    memo[name] = size
    return size


def _walk_chain(start: str, children: dict) -> list:
    """Follow single-child bones from start up to (and including) the first branch point or
    dead end.
    """
    chain = [start]
    node = start
    while True:
        kids = children[node]
        if len(kids) != 1:
            return chain
        node = kids[0]
        chain.append(node)


def classify_skeleton(arm) -> dict | None:
    """Identify the bones these animations need, purely from tree structure. Returns None if
    the armature doesn't have the expected biped shape -- see the module docstring.
    """
    bones = arm.data.bones
    children = _children_map(arm)
    roots = [b.name for b in bones if b.parent is None]
    if len(roots) != 1:
        return None
    hips = roots[0]

    root_children = children[hips]
    if len(root_children) != 3:
        return None

    # The spine continuation has a much bigger subtree than either leg (it goes on to include
    # the head and both arms, fingers included) -- no coordinate axes or naming needed to
    # tell them apart, just which branch is structurally "bigger."
    memo: dict = {}
    sizes = {c: _subtree_size(c, children, memo) for c in root_children}
    spine_start = max(sizes, key=sizes.get)
    legs = [c for c in root_children if c != spine_start]
    if len(legs) != 2 or sizes[spine_start] <= max(sizes[l] for l in legs) * 2:
        return None

    spine_chain = _walk_chain(spine_start, children)
    branch = spine_chain[-1]
    branch_children = children[branch]
    if len(branch_children) != 3:
        return None

    # Head/neck is reliably the smallest of the three subtrees here -- a full arm (shoulder
    # + upper + fore + hand, plus fingers when SkinTokens predicts them) is essentially
    # always bigger than a neck+head chain. This works whether or not fingers are present
    # (unlike checking "does it ever branch again," which only distinguishes arms from head
    # when finger sub-chains actually exist).
    branch_sizes = {c: _subtree_size(c, children, memo) for c in branch_children}
    head_start = min(branch_sizes, key=branch_sizes.get)
    arms = [c for c in branch_children if c != head_start]
    if branch_sizes[arms[0]] <= branch_sizes[head_start] or branch_sizes[arms[1]] <= branch_sizes[head_start]:
        return None

    arm_chains = [_walk_chain(a, children) for a in arms]
    leg_chains = [_walk_chain(l, children) for l in legs]
    head_chain = _walk_chain(head_start, children)

    def pick(chain: list, index: int) -> str:
        return chain[index] if len(chain) > index else chain[-1]

    return {
        "hips": hips,
        "spine_bend": spine_chain[-1],  # the branch bone itself -- closest to the ribcage
        "head": head_chain[-1],  # the tip of the head/neck chain, not the neck base
        "arm_a": pick(arm_chains[0], 1),  # "upper arm" position, one past the shoulder
        "arm_b": pick(arm_chains[1], 1),
        "leg_a_up": leg_chains[0][0],
        "leg_a_knee": leg_chains[0][1] if len(leg_chains[0]) > 1 else None,
        "leg_b_up": leg_chains[1][0],
        "leg_b_knee": leg_chains[1][1] if len(leg_chains[1]) > 1 else None,
    }


def key(pose_bones, frame: int, name: str, degrees_xyz: tuple[float, float, float]) -> None:
    pbone = pose_bones[name]
    pbone.rotation_mode = "XYZ"
    pbone.rotation_euler = tuple(math.radians(d) for d in degrees_xyz)
    pbone.keyframe_insert(data_path="rotation_euler", frame=frame)


def start_action(arm, name: str):
    action = bpy.data.actions.new(name=name)
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = action
    return action


def push_to_nla(arm, action) -> None:
    # Exporting several actions directly via export_animation_mode="ACTIONS" corrupts every
    # action but the first when they key different, non-identical subsets of bones (verified
    # empirically: a lone action bakes correctly, but a second action added alongside it comes
    # back with every one of its keyed channels flattened to the rest pose after export/
    # reimport). Pushing each action onto its own NLA track and exporting with
    # export_animation_mode="NLA_TRACKS" instead produces correct, independent baked clips —
    # this looks like a Blender 5.2 glTF-exporter bug in how it isolates multiple actions
    # under the newer Animation Layers/slotted-action system, not a mistake in the keyframing
    # here. Track name becomes the exported glTF animation clip's name.
    track = arm.animation_data.nla_tracks.new()
    track.name = action.name
    track.strips.new(action.name, 1, action)
    arm.animation_data.action = None


def build_idle(arm, roles: dict) -> None:
    pb = arm.pose.bones
    action = start_action(arm, "Idle")
    # A slow 2s breathing/sway loop. Frame 49 duplicates frame 1's pose so looping is seamless
    # (the exported clip covers frames 1-48; frame 49 exists only to give frame 48's outgoing
    # tangent somewhere sane to interpolate toward).
    poses = {
        1: {
            roles["spine_bend"]: (2, 0, 0),
            roles["head"]: (0, 0, 2),
            roles["arm_a"]: (0, 0, -2),
            roles["arm_b"]: (0, 0, 2),
        },
        25: {
            roles["spine_bend"]: (-1, 0, 0),
            roles["head"]: (0, 0, -2),
            roles["arm_a"]: (0, 0, 1),
            roles["arm_b"]: (0, 0, -1),
        },
    }
    poses[49] = poses[1]
    for frame, bone_rots in poses.items():
        for bone_name, degrees in bone_rots.items():
            key(pb, frame, bone_name, degrees)
    push_to_nla(arm, action)


def build_walk(arm, roles: dict) -> None:
    pb = arm.pose.bones
    action = start_action(arm, "Walk")
    # A stylized 1s (24-frame) walk cycle: contact - passing - contact(mirrored) - passing -
    # contact(=frame 1, closing the loop). Legs swing on X (hip flexion/extension), knees only
    # bend during their own swing phase, arms counter-swing opposite their same-side leg.
    a_up, b_up = roles["leg_a_up"], roles["leg_b_up"]
    a_knee, b_knee = roles["leg_a_knee"], roles["leg_b_knee"]
    arm_a, arm_b = roles["arm_a"], roles["arm_b"]

    poses = {
        1: {a_up: -25, b_up: 25, a_knee: 0, b_knee: -15, arm_a: 20, arm_b: -20},
        7: {a_up: 5, b_up: -5, a_knee: -35, b_knee: 0, arm_a: 0, arm_b: 0},
        13: {a_up: 25, b_up: -25, a_knee: -15, b_knee: 0, arm_a: -20, arm_b: 20},
        19: {a_up: -5, b_up: 5, a_knee: 0, b_knee: -35, arm_a: 0, arm_b: 0},
    }
    poses[25] = poses[1]
    for frame, bone_degs in poses.items():
        for bone_name, degree in bone_degs.items():
            if bone_name is None:  # a leg with no separate knee bone -- skip that key only
                continue
            key(pb, frame, bone_name, (degree, 0, 0))
    push_to_nla(arm, action)


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    bpy.context.scene.render.fps = FPS

    arm = find_armature()
    roles = classify_skeleton(arm) if arm is not None else None
    clips: list[str] = []
    result = {"animated": False, "clips": clips}

    if roles is not None:
        build_idle(arm, roles)
        clips.append("Idle")
        build_walk(arm, roles)
        clips.append("Walk")
        result["animated"] = True

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = (output_dir / f"{args.name}_animated.glb").resolve()
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(
            filepath=str(out_path), export_format="GLB", export_animation_mode="NLA_TRACKS"
        )
        result["animated_glb"] = str(out_path)

    print("RESULT_JSON:" + json.dumps(result))


if __name__ == "__main__":
    main()
