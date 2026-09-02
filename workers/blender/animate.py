"""Bake a small preset animation library (Idle, Walk) onto a rigged character, IF its skeleton
was successfully mapped onto the standard Mixamo bone-name template with a sane hierarchy.

`skeleton_template="Mixamo"` (see docs/RIG.md and the rig worker's `skeleton_template` param)
is NOT guaranteed to produce a correct semantic mapping — verified empirically (see
docs/ANIMATION.md) that SkinTokens' own renamer overlays a fixed name list onto bones in their
existing traversal order, which breaks whenever a real generated skeleton's bone ordering
doesn't match the template's assumed part order. On both real test rigs available when this
was built, the first ~10 bones (Hips through the first arm) came out correct, but everything
past that was nonsense — e.g. "RightShoulder"'s parent came out as "LeftHand".

Rather than trust the renaming blindly, validate_skeleton() checks that the specific
parent-child edges these animations depend on actually hold before keyframing anything. If
they don't, this script exits cleanly having baked nothing — the job still succeeds, just
without an animated output. This means real animated output is currently inconsistent across
generations (both real test rigs on hand fail validation) until SkinTokens' own template
matching improves, or a future generation happens to produce a canonically-ordered skeleton.
The framework itself is verified against a hand-built synthetic skeleton that does pass
validation — see docs/ANIMATION.md for that test.

Run:
  blender --background --python animate.py -- --input rigged.glb --output-dir out/ --name asset
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy

# The subset of the Mixamo template's parent-child edges these two animations actually touch.
# Toe bones are deliberately excluded -- a walk cycle reads fine without animating them, and
# requiring them would reject skeletons that are otherwise perfectly animatable.
REQUIRED_EDGES = [
    ("mixamorig:Spine", "mixamorig:Hips"),
    ("mixamorig:Spine1", "mixamorig:Spine"),
    ("mixamorig:Spine2", "mixamorig:Spine1"),
    ("mixamorig:Neck", "mixamorig:Spine2"),
    ("mixamorig:Head", "mixamorig:Neck"),
    ("mixamorig:LeftShoulder", "mixamorig:Spine2"),
    ("mixamorig:LeftArm", "mixamorig:LeftShoulder"),
    ("mixamorig:LeftForeArm", "mixamorig:LeftArm"),
    ("mixamorig:LeftHand", "mixamorig:LeftForeArm"),
    ("mixamorig:RightShoulder", "mixamorig:Spine2"),
    ("mixamorig:RightArm", "mixamorig:RightShoulder"),
    ("mixamorig:RightForeArm", "mixamorig:RightArm"),
    ("mixamorig:RightHand", "mixamorig:RightForeArm"),
    ("mixamorig:LeftUpLeg", "mixamorig:Hips"),
    ("mixamorig:LeftLeg", "mixamorig:LeftUpLeg"),
    ("mixamorig:LeftFoot", "mixamorig:LeftLeg"),
    ("mixamorig:RightUpLeg", "mixamorig:Hips"),
    ("mixamorig:RightLeg", "mixamorig:RightUpLeg"),
    ("mixamorig:RightFoot", "mixamorig:RightLeg"),
]

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


def validate_skeleton(arm) -> bool:
    bones = arm.data.bones
    if "mixamorig:Hips" not in bones or bones["mixamorig:Hips"].parent is not None:
        return False
    for child_name, parent_name in REQUIRED_EDGES:
        if child_name not in bones or parent_name not in bones:
            return False
        child = bones[child_name]
        if child.parent is None or child.parent.name != parent_name:
            return False
    return True


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


def build_idle(arm) -> None:
    pb = arm.pose.bones
    action = start_action(arm, "Idle")
    # A slow 2s breathing/sway loop. Frame 49 duplicates frame 1's pose so looping is seamless
    # (the exported clip covers frames 1-48; frame 49 exists only to give frame 48's outgoing
    # tangent somewhere sane to interpolate toward).
    poses = {
        1: {
            "mixamorig:Spine2": (2, 0, 0),
            "mixamorig:Head": (0, 0, 2),
            "mixamorig:LeftArm": (0, 0, -2),
            "mixamorig:RightArm": (0, 0, 2),
        },
        25: {
            "mixamorig:Spine2": (-1, 0, 0),
            "mixamorig:Head": (0, 0, -2),
            "mixamorig:LeftArm": (0, 0, 1),
            "mixamorig:RightArm": (0, 0, -1),
        },
    }
    poses[49] = poses[1]
    for frame, bone_rots in poses.items():
        for bone_name, degrees in bone_rots.items():
            key(pb, frame, bone_name, degrees)
    push_to_nla(arm, action)


def build_walk(arm) -> None:
    pb = arm.pose.bones
    action = start_action(arm, "Walk")
    # A stylized 1s (24-frame) walk cycle: contact - passing - contact(mirrored) - passing -
    # contact(=frame 1, closing the loop). Legs swing on X (hip flexion/extension), knees only
    # bend during their own swing phase, arms counter-swing opposite their same-side leg.
    poses = {
        1: {
            "mixamorig:LeftUpLeg": (-25, 0, 0), "mixamorig:RightUpLeg": (25, 0, 0),
            "mixamorig:LeftLeg": (0, 0, 0), "mixamorig:RightLeg": (-15, 0, 0),
            "mixamorig:LeftArm": (20, 0, 0), "mixamorig:RightArm": (-20, 0, 0),
        },
        7: {
            "mixamorig:LeftUpLeg": (5, 0, 0), "mixamorig:RightUpLeg": (-5, 0, 0),
            "mixamorig:LeftLeg": (-35, 0, 0), "mixamorig:RightLeg": (0, 0, 0),
            "mixamorig:LeftArm": (0, 0, 0), "mixamorig:RightArm": (0, 0, 0),
        },
        13: {
            "mixamorig:LeftUpLeg": (25, 0, 0), "mixamorig:RightUpLeg": (-25, 0, 0),
            "mixamorig:LeftLeg": (-15, 0, 0), "mixamorig:RightLeg": (0, 0, 0),
            "mixamorig:LeftArm": (-20, 0, 0), "mixamorig:RightArm": (20, 0, 0),
        },
        19: {
            "mixamorig:LeftUpLeg": (-5, 0, 0), "mixamorig:RightUpLeg": (5, 0, 0),
            "mixamorig:LeftLeg": (0, 0, 0), "mixamorig:RightLeg": (-35, 0, 0),
            "mixamorig:LeftArm": (0, 0, 0), "mixamorig:RightArm": (0, 0, 0),
        },
    }
    poses[25] = poses[1]
    for frame, bone_rots in poses.items():
        for bone_name, degrees in bone_rots.items():
            key(pb, frame, bone_name, degrees)
    push_to_nla(arm, action)


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    bpy.context.scene.render.fps = FPS

    arm = find_armature()
    clips: list[str] = []
    result = {"animated": False, "clips": clips}

    if arm is not None and validate_skeleton(arm):
        build_idle(arm)
        clips.append("Idle")
        build_walk(arm)
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
