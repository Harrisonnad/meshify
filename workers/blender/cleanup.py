"""Mesh cleanup — turn a raw meshgen output into a game-ready asset.

Master plan §2c: decimate to a target tri budget, recalculate normals, apply scale, smart-UV-
unwrap if UVs are missing, set the origin sensibly, export GLB + FBX + STL. This is CPU-side
work, entirely unaffected by the 8GB/Turing limits that shape the rest of the pipeline (see
docs/hardware.md) — see docs/PHASE2_WORKERS.md for why that makes it the lowest-risk stage.

Normal-map baking (high-poly detail -> decimated low-poly) from the master plan's 2c list is
still NOT done — that needs a high-poly cage and visual iteration on margins/cage distance.
Vertex-color baking to a UV texture (the flat kind TripoSR actually produces) IS done, as an
opt-in step (--bake-texture): it's a straight color transfer with no cage-distance guesswork,
so it doesn't need the same blind-iteration caution.

A Laplacian Smooth pass runs after decimation to soften the faceted, slightly-noisy surface
marching-cubes reconstruction tends to leave on thin/disconnected geometry (see docs/MESH_GEN.md
"Known limitation: surface noise" -- that roughness is baked into the reconstruction itself, not
fixable via mesh-extraction or decimation parameters alone; MESH_GEN.md names this smoothing
pass as one of the two real fixes). Laplacian Smooth specifically (with use_volume_preserve on),
not Corrective Smooth: Corrective Smooth's default rest_source is 'ORCO', which on a mesh with
no prior deforming modifier just corrects the smoothing against itself and ends up moving
vertices by nothing (verified empirically -- max delta ~1e-7, i.e. floating-point noise).

lambda_factor is far more sensitive at this mesh's ~1-unit scale than its 10.0 Blender-UI
default suggests: verified empirically on the watering-can test asset (docs/MESH_GEN.md) that
factor<=0.5 is visually a no-op, while factor>=10 melts the can into a blob (handle flattens,
spout shrinks to a stub) within 1-5 iterations. factor=2/iterations=1 is the calibrated sweet
spot -- softens the dents/ripples along the body while keeping the handle and spout recognizable.
MESH_GEN.md already flags this kind of smoothing as "a blunt instrument that could soften real
detail elsewhere too" -- these values are chosen to stay on the safe side of that trade-off, not
to fully eliminate the ripple.

Run:
  blender --background --python cleanup.py -- --input raw.glb --output-dir out/ --name asset
    [--target-tris 8000] [--scale 1.0] [--origin base|center]
    [--smooth-iterations 1] [--smooth-factor 2.0] [--bake-texture] [--bake-size 1024]
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
    parser.add_argument("--smooth-iterations", type=int, default=1, help="0 disables smoothing")
    parser.add_argument("--smooth-factor", type=float, default=2.0, help="Laplacian lambda factor")
    parser.add_argument("--bake-texture", action="store_true", help="Bake vertex colors to a UV texture")
    parser.add_argument("--bake-size", type=int, default=1024)
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


def smooth(obj, iterations: int, factor: float) -> None:
    if iterations <= 0:
        return
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new(name="LaplacianSmooth", type="LAPLACIANSMOOTH")
    mod.lambda_factor = factor
    mod.iterations = iterations
    mod.use_volume_preserve = True
    mod.use_x = mod.use_y = mod.use_z = True
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


def bake_vertex_colors_to_texture(obj, output_dir: Path, name: str, bake_size: int) -> str | None:
    """Bake the mesh's vertex-color attribute into a UV-mapped PNG, and rewire the object's
    material to read that texture instead. No-op (returns None) if there's no vertex-color
    attribute to bake — meshes without one are left exactly as they were.
    """
    color_attr = obj.data.color_attributes.active_color if obj.data.color_attributes else None
    if color_attr is None:
        return None

    scene = bpy.context.scene
    original_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"  # GPU stays free for the ML workers; this is a one-off per job
    scene.render.bake.margin = 16

    mat = bpy.data.materials.new(name=f"{name}_bake")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    attr_node = nodes.new("ShaderNodeVertexColor")
    attr_node.layer_name = color_attr.name
    emit_node = nodes.new("ShaderNodeEmission")
    output_node = nodes.new("ShaderNodeOutputMaterial")
    links.new(attr_node.outputs["Color"], emit_node.inputs["Color"])
    links.new(emit_node.outputs["Emission"], output_node.inputs["Surface"])

    image = bpy.data.images.new(f"{name}_basecolor", width=bake_size, height=bake_size)
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    nodes.active = tex_node

    obj.data.materials.clear()
    obj.data.materials.append(mat)

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.ops.object.bake(type="EMIT")

    # Blender resolves a relative path against the (nonexistent, since nothing is ever saved
    # here) .blend file location, not the process's CWD -- absolute is required for this to
    # land next to the other exports instead of silently failing to write anything.
    texture_path = (output_dir / f"{name}_basecolor.png").resolve()
    image.filepath_raw = str(texture_path)
    image.file_format = "PNG"
    image.save()

    # Rewire for export: Image Texture -> Base Color, so glTF export embeds the baked PNG
    # rather than re-reading the (now-redundant) vertex-color attribute.
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    output_node = nodes.new("ShaderNodeOutputMaterial")
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output_node.inputs["Surface"])

    scene.render.engine = original_engine
    return str(texture_path)


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
    smooth(obj, args.smooth_iterations, args.smooth_factor)
    fix_normals(obj)
    ensure_uvs(obj)

    texture_path = None
    if args.bake_texture:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        texture_path = bake_vertex_colors_to_texture(obj, output_dir, args.name, args.bake_size)

    apply_scale_and_origin(obj, args.scale, args.origin)
    tris_after = sum(len(p.vertices) - 2 for p in obj.data.polygons)

    paths = export_all(obj, Path(args.output_dir), args.name)
    if texture_path:
        paths["texture"] = texture_path

    # A single machine-readable line the wrapping worker can grep out of stdout.
    print("RESULT_JSON:" + json.dumps({
        "tris_before": tris_before,
        "tris_after": tris_after,
        "watertight": None,  # Blender doesn't check this directly; meshgen already confirmed it
        "smoothed": args.smooth_iterations > 0,
        **paths,
    }))


if __name__ == "__main__":
    main()
