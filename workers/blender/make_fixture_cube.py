"""Generate the fixed-cube fixture that stub workers return.

Master plan §1: "you can develop the UI against stubbed workers that return a fixed cube."
This is that cube. It also doubles as the first smoke test that headless Blender export
works on this machine, which is the one part of the pipeline the 8GB/Turing limits can't touch.

Run:
  blender --background --python make_fixture_cube.py -- --output fixtures/cube.glb
"""
import argparse
import sys

import bpy


def parse_args() -> argparse.Namespace:
    # Blender passes its own argv; everything after "--" is ours.
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", type=float, default=1.0, help="Cube size in meters")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=args.size)
    cube = bpy.context.active_object
    cube.name = "StubCube"

    # Origin at the base, not the center — the plan's convention for props (§2c).
    cube.location = (0.0, 0.0, args.size / 2.0)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.uv.smart_project(angle_limit=1.15192)  # 66 degrees, Blender's default
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.export_scene.gltf(filepath=args.output, export_format="GLB")
    print(f"[fixture] wrote {args.output}")


if __name__ == "__main__":
    main()
