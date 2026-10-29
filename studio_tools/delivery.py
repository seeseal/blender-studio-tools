import os
import shutil

import bpy
from bpy.props import BoolProperty, StringProperty

_REL = {"options": {"PATH_SUPPORTS_BLEND_RELATIVE"}} if bpy.app.version >= (4, 5, 0) else {}


# ------------------------------------------------------------------ helpers

def _all_id_collections():
    for attr in dir(bpy.data):
        coll = getattr(bpy.data, attr, None)
        if isinstance(coll, bpy.types.bpy_prop_collection) and attr not in {
            "window_managers", "screens", "workspaces", "libraries"
        }:
            yield attr, coll


def linked_ids():
    out = []
    for attr, coll in _all_id_collections():
        try:
            for idb in coll:
                if getattr(idb, "library", None):
                    out.append(idb)
        except TypeError:
            pass
    return out


def compositor_tree(scene):
    tree = getattr(scene, "compositing_node_group", None)  # Blender 5.x
    if tree is None:
        tree = getattr(scene, "node_tree", None)  # Blender 4.x
    return tree


def _output_nodes(tree, kind):
    if kind == "MATERIAL":
        types = {"ShaderNodeOutputMaterial", "ShaderNodeOutputAOV"}
    elif kind == "WORLD":
        types = {"ShaderNodeOutputWorld"}
    else:  # compositor
        types = {"NodeGroupOutput", "CompositorNodeComposite", "CompositorNodeViewer",
                 "CompositorNodeOutputFile"}
    return [n for n in tree.nodes if n.bl_idname in types]


def _kept_nodes(tree, outputs):
    keep = set()
    stack = list(outputs)
    while stack:
        node = stack.pop()
        if node in keep:
            continue
        keep.add(node)
        for sock in node.inputs:
            for link in sock.links:
                if not link.is_muted:
                    stack.append(link.from_node)
    # frames that still hold a kept node, and their parent frames
    changed = True
    while changed:
        changed = False
        for n in tree.nodes:
            if n.parent and n in keep and n.parent not in keep:
                keep.add(n.parent)
                changed = True
    return keep


def node_trees_to_clean():
    for mat in bpy.data.materials:
        if mat.node_tree and not mat.library:
            yield mat.name, mat.node_tree, "MATERIAL"
    for world in bpy.data.worlds:
        if world.node_tree and not world.library:
            yield "World: " + world.name, world.node_tree, "WORLD"
    for scene in bpy.data.scenes:
        tree = compositor_tree(scene)
        if tree:
            yield "Compositor: " + scene.name, tree, "COMPOSITOR"


def unused_nodes():
    found = []
    for label, tree, kind in node_trees_to_clean():
        outputs = _output_nodes(tree, kind)
        if not outputs:
            continue  # nothing to judge against, leave it alone
        keep = _kept_nodes(tree, outputs)
        dead = [n for n in tree.nodes if n not in keep]
        if dead:
            found.append((label, tree, dead))
    return found


def unused_slot_count(obj):
    if obj.type != "MESH" or len(obj.material_slots) < 2:
        return 0
    used = {p.material_index for p in obj.data.polygons}
    return sum(1 for i in range(len(obj.material_slots)) if i not in used)


def unpacked_images():
    out = []
    for img in bpy.data.images:
        if img.source in {"FILE", "SEQUENCE", "TILED"} and not img.packed_file and not img.library:
            out.append(img)
    return out


def cache_dirs():
    """Disk caches that must travel with the .blend."""
    out = []
    if not bpy.data.filepath:
        return out
    base = os.path.dirname(bpy.data.filepath)
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    blendcache = os.path.join(base, "blendcache_" + stem)
    if os.path.isdir(blendcache):
        out.append(blendcache)
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "FLUID" and getattr(mod, "fluid_type", "") == "DOMAIN":
                path = bpy.path.abspath(mod.domain_settings.cache_directory)
                if os.path.isdir(path) and path not in out:
                    out.append(path)
    return out


# ------------------------------------------------------------------ report

def build_report(context):
    wm = context.window_manager
    wm.studio_report.clear()

    def add(text, level="INFO"):
        item = wm.studio_report.add()
        item.text = text
        item.level = level

    if not bpy.data.filepath:
        add("File is not saved yet", "ERROR")
    elif bpy.data.is_dirty:
        add("File has unsaved changes", "WARNING")

    imgs = unpacked_images()
    if imgs:
        add(f"{len(imgs)} texture(s) not packed", "WARNING")
    missing = [i for i in imgs if not os.path.exists(bpy.path.abspath(i.filepath))]
    if missing:
        add(f"{len(missing)} texture file(s) missing on disk", "ERROR")

    links = linked_ids()
    if links:
        add(f"{len(links)} item(s) linked from other files", "WARNING")

    slots = sum(unused_slot_count(o) for o in bpy.data.objects)
    if slots:
        add(f"{slots} unused material slot(s)", "INFO")

    nodes = sum(len(dead) for _, _, dead in unused_nodes())
    if nodes:
        add(f"{nodes} unconnected node(s)", "INFO")

    orphans = sum(
        1 for _, coll in _all_id_collections() for idb in coll
        if hasattr(idb, "users") and idb.users == 0 and not getattr(idb, "use_fake_user", False)
    )
    if orphans:
        add(f"{orphans} unused data-block(s)", "INFO")

    caches = cache_dirs()
    for c in caches:
        add("Cache to copy: " + os.path.basename(c), "INFO")

    out = context.scene.render.filepath
    if out and not out.startswith("//"):
        add("Render output path is absolute", "INFO")

    if len(wm.studio_report) == 0 or all(i.level == "INFO" and i.text.startswith("Cache") for i in wm.studio_report):
        add("Scene is clean", "CHECKMARK")


# ------------------------------------------------------------------ operators

class STUDIO_OT_check_scene(bpy.types.Operator):
    bl_idname = "studio.check_scene"
    bl_label = "Check Scene"
    bl_description = "List anything that would break or bloat a hand-off"

    def execute(self, context):
        build_report(context)
        return {"FINISHED"}


class STUDIO_OT_clean_scene(bpy.types.Operator):
    bl_idname = "studio.clean_scene"
    bl_label = "Clean Scene"
    bl_description = "Pack textures, make linked items local, remove unused slots, nodes and data"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.studio_delivery
        done = []

        if s.pack_textures:
            try:
                bpy.ops.file.pack_all()
                done.append("textures packed")
            except RuntimeError as err:
                self.report({"WARNING"}, f"Pack failed: {err}")

        if s.make_local:
            ids = linked_ids()
            for idb in ids:
                try:
                    idb.make_local()
                except RuntimeError:
                    pass
            for lib in list(bpy.data.libraries):
                still = any(getattr(i, "library", None) == lib for i in linked_ids())
                if not still:
                    bpy.data.libraries.remove(lib)
            if ids:
                done.append(f"{len(ids)} linked item(s) made local")

        if s.remove_unused_slots and context.mode == "OBJECT":
            view_layer = context.view_layer
            prev_active = view_layer.objects.active
            prev_sel = [o for o in context.selected_objects]
            removed = 0
            for obj in [o for o in context.scene.objects if unused_slot_count(o)]:
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                view_layer.objects.active = obj
                before = len(obj.material_slots)
                bpy.ops.object.material_slot_remove_unused()
                removed += before - len(obj.material_slots)
            for o in context.selected_objects:
                o.select_set(False)
            for o in prev_sel:
                o.select_set(True)
            view_layer.objects.active = prev_active
            if removed:
                done.append(f"{removed} unused slot(s) removed")

        if s.remove_unused_nodes:
            count = 0
            for _, tree, dead in unused_nodes():
                for n in dead:
                    tree.nodes.remove(n)
                    count += 1
            if count:
                done.append(f"{count} unconnected node(s) removed")

        if s.purge_orphans:
            n = bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
            if n:
                done.append(f"{n} unused data-block(s) purged")

        if s.relative_paths and bpy.data.filepath:
            bpy.ops.file.make_paths_relative()
            out = context.scene.render.filepath
            if out and not out.startswith("//"):
                try:
                    context.scene.render.filepath = bpy.path.relpath(out)
                except ValueError:
                    pass  # different drive on Windows
            done.append("paths made relative")

        build_report(context)
        self.report({"INFO"}, ", ".join(done) if done else "Nothing to clean")
        return {"FINISHED"}


class STUDIO_OT_package_delivery(bpy.types.Operator):
    bl_idname = "studio.package_delivery"
    bl_label = "Package Delivery"
    bl_description = "Save a copy of this scene and its caches into a clean delivery folder"

    def execute(self, context):
        s = context.scene.studio_delivery
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the file first")
            return {"CANCELLED"}
        root = bpy.path.abspath(s.delivery_root)
        if not root:
            self.report({"ERROR"}, "Pick a delivery folder")
            return {"CANCELLED"}
        project = "".join(c for c in (s.project_name or "Delivery") if c not in '<>:"/\\|?*').strip() or "Delivery"
        blend_name = s.blend_name.strip() or os.path.splitext(os.path.basename(bpy.data.filepath))[0]

        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()

        dest = os.path.join(root, project)
        if os.path.abspath(dest) == os.path.abspath(os.path.dirname(bpy.data.filepath)):
            self.report({"ERROR"}, "Delivery folder must differ from the working folder")
            return {"CANCELLED"}
        folders = [
            ("Assets", "Models", "Props"),
            ("Assets", "References"),
            ("Animation", "Scene_Files"),
            ("Exports", "Delivery"),
        ]
        if s.final_exports_folder:
            folders.append(("Exports", "Final_Exports"))
        for parts in folders:
            os.makedirs(os.path.join(dest, *parts), exist_ok=True)

        scene_dir = os.path.join(dest, "Animation", "Scene_Files")
        new_blend = os.path.join(scene_dir, blend_name + ".blend")

        caches = cache_dirs()
        old_stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
        base = os.path.dirname(bpy.data.filepath)

        bpy.ops.wm.save_as_mainfile(filepath=new_blend, copy=True, relative_remap=True, compress=True)

        copied = 0
        for c in caches:
            if os.path.basename(c) == "blendcache_" + old_stem:
                target = os.path.join(scene_dir, "blendcache_" + blend_name)
            else:
                try:
                    rel = os.path.relpath(c, base)
                except ValueError:
                    rel = os.path.basename(c)
                if rel.startswith(".."):
                    rel = os.path.basename(c)
                target = os.path.join(scene_dir, rel)
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(c, target)
            copied += 1

        s.last_package = new_blend
        self.report({"INFO"}, f"Packaged to {dest} ({copied} cache folder(s) copied)")
        if s.open_when_done:
            bpy.ops.wm.path_open(filepath=dest)
        return {"FINISHED"}


# ------------------------------------------------------------------ UI

class STUDIO_PT_delivery(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Delivery Packager"

    def draw(self, context):
        layout = self.layout
        s = context.scene.studio_delivery
        wm = context.window_manager

        layout.operator("studio.check_scene", icon="VIEWZOOM")
        if len(wm.studio_report):
            box = layout.box()
            for item in wm.studio_report:
                box.label(text=item.text, icon=item.level)

        col = layout.column(heading="Clean")
        col.prop(s, "pack_textures")
        col.prop(s, "make_local")
        col.prop(s, "remove_unused_slots")
        col.prop(s, "remove_unused_nodes")
        col.prop(s, "purge_orphans")
        col.prop(s, "relative_paths")
        layout.operator("studio.clean_scene", icon="BRUSH_DATA")

        layout.separator()
        col = layout.column()
        col.prop(s, "delivery_root")
        col.prop(s, "project_name")
        col.prop(s, "blend_name")
        col.prop(s, "final_exports_folder")
        col.prop(s, "open_when_done")
        layout.operator("studio.package_delivery", icon="PACKAGE")
        if s.last_package:
            layout.label(text="Last: " + os.path.basename(s.last_package), icon="FILE_BLEND")


class StudioReportItem(bpy.types.PropertyGroup):
    text: StringProperty()
    level: StringProperty(default="INFO")


class StudioDeliverySettings(bpy.types.PropertyGroup):
    pack_textures: BoolProperty(name="Pack textures", default=True)
    make_local: BoolProperty(name="Make linked items local", default=True)
    remove_unused_slots: BoolProperty(name="Remove unused material slots", default=True)
    remove_unused_nodes: BoolProperty(name="Remove unconnected nodes", default=True)
    purge_orphans: BoolProperty(name="Purge unused data", default=True)
    relative_paths: BoolProperty(name="Make paths relative", default=True)
    delivery_root: StringProperty(name="Folder", subtype="DIR_PATH", **_REL)
    project_name: StringProperty(name="Project", default="Delivery")
    blend_name: StringProperty(name="Scene name", description="Leave empty to keep the current file name")
    final_exports_folder: BoolProperty(name="Add Final_Exports folder", default=False)
    open_when_done: BoolProperty(name="Open folder when done", default=True)
    last_package: StringProperty()


classes = (
    StudioReportItem,
    StudioDeliverySettings,
    STUDIO_OT_check_scene,
    STUDIO_OT_clean_scene,
    STUDIO_OT_package_delivery,
    STUDIO_PT_delivery,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.studio_delivery = bpy.props.PointerProperty(type=StudioDeliverySettings)
    bpy.types.WindowManager.studio_report = bpy.props.CollectionProperty(type=StudioReportItem)


def unregister():
    del bpy.types.WindowManager.studio_report
    del bpy.types.Scene.studio_delivery
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
