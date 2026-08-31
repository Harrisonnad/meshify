"""Mesh cleanup — turn a raw meshgen output into a game-ready asset.

Master plan §2c: decimate to a target tri budget, recalculate normals, apply scale, smart-UV-
unwrap if UVs are missing, set the origin sensibly, export GLB + FBX + STL. This is CPU-side
work, entirely unaffected by the 8GB/Turing limits that shape the rest of the pipeline (see
docs/hardware.md) — see docs/PHASE2_WORKERS.md for why that makes it the lowest-risk stage.

Normal-map baking (high-poly detail -> decimated low-poly) from the master plan's 2c list is
deliberately NOT done here yet — it needs visual iteration to get bake margins/cage distance
right, which isn't practical blind. Vertex colors are kept as-is for now; baking is a
follow-up once there's a way to actually look at the result.

Run:
  blender --background --python cleanup.py -- --input raw.glb --output-dir out/ --name asset
    [--target-tris 8000] [--scale 1.0] [--origin base|center]
"""
import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to raw.glb/.obj from meshgen")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--name", required=True, help="Base filename for the three exports")
    parser.add_argument("--target-tris", type=int, default=8000, help="Godot 4 low-poly default")
    parser.add_argument("--scale", type=float, default=1.0, help="Uniform scale factor")
    parser.add_argument("--origin", choices=["base", "center"], default="base")
    return parser.parse_args(argv)


def import_mesh(path: Path):
    if path.suffix.lower() == ".glb" or path.suffix.lower() == ".gltf":
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif path.suffix.lower() == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        raise ValueError(f"unsupported input format: {path.suffix}")

    meshes = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"no mesh objects found in {path}")
    if len(meshes) == 1:
        return meshes[0]

    bpy.context.view_layer.objects.active = meshes[0]
    for o in meshes:
        o.select_set(True)
    bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def decimate(obj, target_tris: int) -> None:
    tri_count = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    if tri_count <= target_tris:
        return
    ratio = max(target_tris / tri_count, 0.01)
    mod = obj.modifiers.new(name="Decimate", type="DECIMATE")
    mod.ratio = ratio
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def fix_normals(obj) -> None:
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.shade_smooth()


def ensure_uvs(obj) -> None:
    if obj.data.uv_layers:
        return
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15192)  # 66 degrees, Blender's default
    bpy.ops.object.mode_set(mode="OBJECT")


def apply_scale_and_origin(obj, scale: float, origin: str) -> None:
    obj.scale = (scale, scale, scale)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if origin == "base":
        verts = [obj.matrix_world @ v.co for v in obj.data.vertices]
        min_z = min(v.z for v in verts)
        cx = sum(v.x for v in verts) / len(verts)
        cy = sum(v.y for v in verts) / len(verts)
        bpy.context.scene.cursor.location = (cx, cy, min_z)
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    else:
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")

    obj.location = (0.0, 0.0, 0.0)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def export_all(obj, output_dir: Path, name: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    paths = {}

    glb_path = output_dir / f"{name}.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", use_selection=True)
    paths["glb"] = str(glb_path)

    fbx_path = output_dir / f"{name}.fbx"
    bpy.ops.export_scene.fbx(filepath=str(fbx_path), use_selection=True)
    paths["fbx"] = str(fbx_path)

    stl_path = output_dir / f"{name}.stl"
    bpy.ops.wm.stl_export(filepath=str(stl_path), export_selected_objects=True)
    paths["stl"] = str(stl_path)

    return paths


def main() -> None:
    args = parse_args()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = import_mesh(Path(args.input))

    tris_before = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    decimate(obj, args.target_tris)
    fix_normals(obj)
    ensure_uvs(obj)
    apply_scale_and_origin(obj, args.scale, args.origin)
    tris_after = sum(len(p.vertices) - 2 for p in obj.data.polygons)

    paths = export_all(obj, Path(args.output_dir), args.name)

    # A single machine-readable line the wrapping worker can grep out of stdout.
    print("RESULT_JSON:" + json.dumps({
        "tris_before": tris_before,
        "tris_after": tris_after,
        "watertight": None,  # Blender doesn't check this directly; meshgen already confirmed it
        **paths,
    }))


if __name__ == "__main__":
    main()
