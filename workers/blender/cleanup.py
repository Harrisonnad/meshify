"""Mesh cleanup — turn a raw meshgen output into a game-ready asset.

Master plan §2c: decimate to a target tri budget, recalculate normals, apply scale, smart-UV-
unwrap if UVs are missing, set the origin sensibly, export GLB + FBX + STL. This is CPU-side
work, entirely unaffected by the 8GB/Turing limits that shape the rest of the pipeline (see
docs/hardware.md) — see docs/PHASE2_WORKERS.md for why that makes it the lowest-risk stage.

Normal-map baking (--bake-normal) IS done now: a full-resolution duplicate of the raw
meshgen output is kept alongside the working mesh, and a selected-to-active Cycles bake
transfers its surface detail onto the final (decimated/smoothed/retopologized) low-poly's UVs.
Calibrated empirically (see docs/PBR.md) rather than blind: `cage_extrusion=0.02` and
`max_ray_distance=0.05` avoid ray-miss artifacts on this project's test assets at their normal
~1-unit scale. Also verified empirically that — unlike the AO bake below — a NORMAL bake's
raw pixel values are identical regardless of the target image's colorspace tag, since Blender
treats "Normal" as a data pass that bypasses the light-transport color pipeline AO/EMIT go
through; it's tagged Non-Color anyway for correctness when read back into a Normal Map node.

Vertex-color baking to a UV texture (the flat kind TripoSR actually produces) IS done too, as
an opt-in step (--bake-texture): it's a straight color transfer with no cage-distance
guesswork, so it didn't need the same blind-iteration caution the normal map did.

Roughness/metallic are exposed as flat factors (--roughness-factor/--metallic-factor), not
baked maps -- getting real per-pixel roughness/metallic needs a material-understanding model
this project doesn't have (see docs/PBR.md's "Compared to Meshy 7" discussion); a sensibly
chosen constant is still a real improvement over Blender's opaque default (0.5 roughness, 0
metallic) for props that are neither glossy nor metal.

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

Retopology (--retopology) runs Blender's built-in QuadriFlow remesher after decimation/
smoothing, trading the ratio-decimate's triangle soup for game-artist-style quad edge flow at
roughly the same tri budget. QuadriFlow requires manifold input with consistent face normals —
that's exactly what fix_normals() already guarantees, which is why retopology runs after it,
not before. QuadriFlow also throws away UVs and vertex colors as a side effect of generating
new topology, so retopologize() transfers the vertex-color attribute onto the new mesh from a
pre-remesh duplicate before discarding it (verified empirically: colors survive intact, indexed
by nearest source polygon normal), and ensure_uvs() naturally regenerates UVs afterward since
none remain. If QuadriFlow can't remesh the input (rare, but possible after heavy smoothing
distorts thin geometry into a non-manifold state), retopologize() leaves the mesh as-is rather
than failing the job.

fix_normals() runs again after a successful retopology, and does more than the single
`normals_make_consistent(inside=False)` call its name implies: verified empirically that on a
QuadriFlow-remeshed watering-can mesh, that call's outward/inward heuristic got it backwards
100% of the time (every face pointing inward) -- invisible to a plain vertex-color EMIT bake
(which doesn't care about normal direction) but fatal to the AO bake added alongside it (an
inverted mesh reads as almost fully self-occluded, i.e. solid black) and would equally break
rendering in any backface-culling game engine. fix_normals() now checks the result with a
majority vote of (face-center - mesh-centroid)·normal across all polygons and flips the whole
mesh if most disagree, before shading.

Run:
  blender --background --python cleanup.py -- --input raw.glb --output-dir out/ --name asset
    [--target-tris 8000] [--scale 1.0] [--origin base|center]
    [--smooth-iterations 1] [--smooth-factor 2.0] [--retopology]
    [--bake-texture] [--bake-normal] [--bake-size 2048]
    [--roughness-factor 0.6] [--metallic-factor 0.0]
"""
import argparse
import json
import sys
from pathlib import Path

import bpy
import mathutils


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
    parser.add_argument("--retopology", action="store_true", help="QuadriFlow remesh for clean quad edge flow")
    parser.add_argument("--bake-texture", action="store_true", help="Bake vertex colors to a UV texture")
    parser.add_argument("--bake-normal", action="store_true", help="Bake high-poly surface detail to a normal map")
    parser.add_argument("--bake-size", type=int, default=2048)
    parser.add_argument("--roughness-factor", type=float, default=0.6)
    parser.add_argument("--metallic-factor", type=float, default=0.0)
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


def retopologize(obj, target_faces: int) -> bool:
    """QuadriFlow-remesh obj to ~target_faces quads, preserving its vertex colors across the
    remesh via a data-transfer from a pre-remesh duplicate. Returns whether it actually ran —
    QuadriFlow can decline (non-manifold input) without raising, and the caller needs to know
    whether normals/UVs need regenerating.
    """
    if target_faces <= 0:
        return False

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.duplicate()
    color_source = bpy.context.view_layer.objects.active
    had_colors = bool(color_source.data.color_attributes)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    result = bpy.ops.object.quadriflow_remesh(
        target_faces=target_faces,
        use_mesh_symmetry=False,
        use_preserve_sharp=False,
        use_preserve_boundary=False,
    )
    if result != {"FINISHED"}:
        bpy.ops.object.select_all(action="DESELECT")
        color_source.select_set(True)
        bpy.context.view_layer.objects.active = color_source
        bpy.ops.object.delete()
        bpy.context.view_layer.objects.active = obj
        return False

    if had_colors:
        # object.data_transfer transfers FROM the active object TO the other selected ones —
        # opposite convention from the DATA_TRANSFER modifier's own .object field.
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        color_source.select_set(True)
        bpy.context.view_layer.objects.active = color_source
        bpy.ops.object.data_transfer(data_type="COLOR_CORNER", loop_mapping="NEAREST_POLYNOR", use_create=True)

    bpy.ops.object.select_all(action="DESELECT")
    color_source.select_set(True)
    bpy.context.view_layer.objects.active = color_source
    bpy.ops.object.delete()
    bpy.context.view_layer.objects.active = obj
    return True


def fix_normals(obj) -> None:
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

    # normals_make_consistent's outward/inward choice is a heuristic and can get it backwards
    # on freshly-generated topology (verified: 100% inward on a QuadriFlow-remeshed mesh) --
    # a majority vote against the mesh centroid catches and corrects a globally-inverted result.
    mesh = obj.data
    center = sum((v.co for v in mesh.vertices), mathutils.Vector()) / len(mesh.vertices)
    inward = sum(1 for p in mesh.polygons if (p.center - center).dot(p.normal) <= 0)
    if inward > len(mesh.polygons) / 2:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.flip_normals()
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


def bake_pbr_maps(
    obj,
    high_poly,
    output_dir: Path,
    name: str,
    bake_size: int,
    bake_texture: bool,
    bake_normal: bool,
    roughness_factor: float,
    metallic_factor: float,
) -> dict:
    """Bake whichever of {base color + AO, normal} were requested, and build a Principled BSDF
    material carrying them plus flat roughness/metallic factors. Returns {} (mesh left exactly
    as it was, no material created) if nothing ends up baked -- e.g. bake_texture was requested
    but the mesh has no vertex colors to bake from.

    AO is a real geometry-derived bake (Cycles' own AO pass) and the normal map is a real
    high-poly-to-low-poly detail transfer -- neither needs material understanding to be
    correct. Roughness/metallic are NOT baked maps, just flat factors; see the module
    docstring and docs/PBR.md for why real per-pixel values are out of scope here.
    """
    color_attr = obj.data.color_attributes.active_color if obj.data.color_attributes else None
    if bake_texture and color_attr is None:
        bake_texture = False
    if bake_normal and high_poly is None:
        bake_normal = False
    if not bake_texture and not bake_normal:
        return {}

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
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    result: dict = {}
    color_image = None

    if bake_texture:
        attr_node = nodes.new("ShaderNodeVertexColor")
        attr_node.layer_name = color_attr.name
        emit_node = nodes.new("ShaderNodeEmission")
        output_node = nodes.new("ShaderNodeOutputMaterial")
        links.new(attr_node.outputs["Color"], emit_node.inputs["Color"])
        links.new(emit_node.outputs["Emission"], output_node.inputs["Surface"])

        color_image = bpy.data.images.new(f"{name}_basecolor", width=bake_size, height=bake_size)
        color_tex_node = nodes.new("ShaderNodeTexImage")
        color_tex_node.image = color_image
        nodes.active = color_tex_node

        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.ops.object.bake(type="EMIT")

        # Blender resolves a relative path against the (nonexistent, since nothing is ever
        # saved here) .blend file location, not the process's CWD -- absolute is required for
        # this to land next to the other exports instead of silently failing to write anything.
        texture_path = (output_dir / f"{name}_basecolor.png").resolve()
        color_image.filepath_raw = str(texture_path)
        color_image.file_format = "PNG"
        color_image.save()
        result["texture"] = str(texture_path)

        # AO is a standalone geometry-driven bake type: it needs a target image node active in
        # the material to know where to write, but doesn't care what the shader graph does, so
        # it can reuse the same node tree without touching the base-color hookup yet.
        ao_image = bpy.data.images.new(f"{name}_ao", width=bake_size, height=bake_size)
        ao_tex_node = nodes.new("ShaderNodeTexImage")
        ao_tex_node.image = ao_image
        nodes.active = ao_tex_node
        bpy.ops.object.bake(type="AO")

        ao_path = (output_dir / f"{name}_ao.png").resolve()
        ao_image.filepath_raw = str(ao_path)
        ao_image.file_format = "PNG"
        # Deliberately NOT marked Non-Color: verified empirically that doing so before save()
        # writes near-black output (the raw linear AO factors are low enough that skipping the
        # sRGB encode crushes them to ~0 in 8-bit). Default colorspace produces a correctly
        # visible map; this is meant as a viewable/multiply-in AO texture, not a strict linear
        # PBR channel.
        ao_image.save()
        nodes.remove(ao_tex_node)
        result["ao"] = str(ao_path)

    normal_image = None
    if bake_normal:
        # Calibrated empirically on this project's test assets (~1-unit scale) -- see
        # docs/PBR.md. cage_extrusion pushes the low-poly cage outward before ray-casting to
        # the high-poly surface (too small and rays miss surface detail sitting outside the
        # low-poly's exact shape after decimation; too large and rays pick up neighboring
        # geometry that isn't actually "this" surface). max_ray_distance caps how far a ray
        # travels looking for the high-poly surface, to avoid it passing through thin geometry
        # and wrongly hitting the far side.
        scene.render.bake.use_selected_to_active = True
        scene.render.bake.cage_extrusion = 0.02
        scene.render.bake.max_ray_distance = 0.05

        normal_image = bpy.data.images.new(f"{name}_normal", width=bake_size, height=bake_size)
        normal_tex_node = nodes.new("ShaderNodeTexImage")
        normal_tex_node.image = normal_image
        nodes.active = normal_tex_node

        bpy.ops.object.select_all(action="DESELECT")
        high_poly.select_set(True)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj  # active = bake target; selected = source
        bpy.ops.object.bake(type="NORMAL")

        scene.render.bake.use_selected_to_active = False  # don't leak into any later bake

        normal_path = (output_dir / f"{name}_normal.png").resolve()
        normal_image.filepath_raw = str(normal_path)
        normal_image.file_format = "PNG"
        # Save BEFORE tagging Non-Color, not after: verified empirically that tagging
        # Non-Color before save() writes a solid-black file, but only when this bake follows
        # other bakes (EMIT/AO) earlier in the same session -- an isolated normal-only bake
        # saves fine either way. In-memory pixel values read correctly right up to and after
        # save() either way, so this looks like a stale color-management cache issue in
        # Blender's file-write path specifically, not a problem with the baked data itself.
        # Re-tagging after save doesn't touch the already-written file; it's there so the
        # Normal Map shader node built below (and glTF export reading through it) treats the
        # in-memory image as data, not a display color.
        normal_image.save()
        normal_image.colorspace_settings.name = "Non-Color"
        nodes.remove(normal_tex_node)
        result["normal"] = str(normal_path)

    # Final shading graph for export: fresh Principled BSDF with whichever maps were baked
    # wired in, plus the flat roughness/metallic factors regardless of what was baked.
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = roughness_factor
    bsdf.inputs["Metallic"].default_value = metallic_factor
    output_node = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], output_node.inputs["Surface"])

    if color_image is not None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = color_image
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    elif color_attr is not None:
        # Baking a normal map alone shouldn't throw away existing vertex colors -- link them
        # live (unbaked) so the mesh still shows its original color through the new material.
        attr_node = nodes.new("ShaderNodeVertexColor")
        attr_node.layer_name = color_attr.name
        links.new(attr_node.outputs["Color"], bsdf.inputs["Base Color"])

    if normal_image is not None:
        normal_tex_node = nodes.new("ShaderNodeTexImage")
        normal_tex_node.image = normal_image
        normal_map_node = nodes.new("ShaderNodeNormalMap")
        links.new(normal_tex_node.outputs["Color"], normal_map_node.inputs["Color"])
        links.new(normal_map_node.outputs["Normal"], bsdf.inputs["Normal"])

    scene.render.engine = original_engine
    return result


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

    # A normal-map bake needs the *original* full-resolution surface as its detail source --
    # decimate/smooth/retopology below all destructively simplify obj, so this duplicate is
    # taken before any of that runs, regardless of how aggressively obj ends up processed.
    high_poly = None
    if args.bake_normal:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.duplicate()
        high_poly = bpy.context.view_layer.objects.active
        high_poly.name = f"{args.name}_highpoly_source"
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    tris_before = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    decimate(obj, args.target_tris)
    smooth(obj, args.smooth_iterations, args.smooth_factor)
    fix_normals(obj)

    retopologized = False
    if args.retopology:
        retopologized = retopologize(obj, max(args.target_tris // 2, 50))
        if retopologized:
            fix_normals(obj)  # remesh produces new geometry -- normals/shading need redoing

    ensure_uvs(obj)

    baked = {}
    if args.bake_texture or args.bake_normal:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        baked = bake_pbr_maps(
            obj,
            high_poly,
            output_dir,
            args.name,
            args.bake_size,
            args.bake_texture,
            args.bake_normal,
            args.roughness_factor,
            args.metallic_factor,
        )

    if high_poly is not None:
        bpy.data.objects.remove(high_poly, do_unlink=True)

    apply_scale_and_origin(obj, args.scale, args.origin)
    tris_after = sum(len(p.vertices) - 2 for p in obj.data.polygons)

    paths = export_all(obj, Path(args.output_dir), args.name)
    paths.update(baked)

    # A single machine-readable line the wrapping worker can grep out of stdout.
    print("RESULT_JSON:" + json.dumps({
        "tris_before": tris_before,
        "tris_after": tris_after,
        "watertight": None,  # Blender doesn't check this directly; meshgen already confirmed it
        "smoothed": args.smooth_iterations > 0,
        "retopologized": retopologized,
        "roughness_factor": args.roughness_factor,
        "metallic_factor": args.metallic_factor,
        **paths,
    }))


if __name__ == "__main__":
    main()
