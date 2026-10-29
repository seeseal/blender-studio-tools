import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_NUMBER = re.compile(r"\.\d{3,}$")


def _title(text):
    words = re.split(r"(\s+)", text)
    out = []
    for w in words:
        if not w.strip() or w.isupper() or any(c.isdigit() for c in w):
            out.append(w)  # keep spaces, acronyms (LL, HDRI) and things like 01
        else:
            out.append(w[:1].upper() + w[1:])
    return "".join(out)


def new_name(name, s):
    n = name
    if s.find:
        n = n.replace(s.find, s.replace)
    if s.strip_brackets:
        n = _BRACKETS.sub("", n)
    if s.strip_numbers:
        n = _NUMBER.sub("", n)
    if s.underscores:
        n = n.replace("_", " ")
    n = re.sub(r"\s{2,}", " ", n).strip(" -")
    if s.title_case:
        n = _title(n)
    if s.prefix and not n.startswith(s.prefix):
        n = s.prefix + n
    if s.suffix and not n.endswith(s.suffix):
        n = n + s.suffix
    return n or name


def _targets(context, s):
    objs = list(context.selected_objects) if s.scope == "SELECTED" else list(context.scene.objects)
    items = []
    if s.do_objects:
        items += objs
    if s.do_materials:
        mats = {slot.material for o in objs for slot in o.material_slots if slot.material}
        items += sorted(mats, key=lambda m: m.name)
    if s.do_collections and s.scope == "ALL":
        items += list(context.scene.collection.children_recursive)
    return objs, [i for i in items if not getattr(i, "library", None)]


class STUDIO_OT_quick_rename(bpy.types.Operator):
    bl_idname = "studio.quick_rename"
    bl_label = "Rename"
    bl_description = "Clean up names with the options above"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.studio_rename
        objs, items = _targets(context, s)
        plan = [(i, new_name(i.name, s)) for i in items]
        plan = [(i, n) for i, n in plan if n != i.name]
        # two passes so names that swap places don't collide into .001
        for i, n in plan:
            i.name = "__studio_tmp__" + n
        changed = 0
        for i, n in plan:
            i.name = n
            changed += 1
        if s.sync_data:
            for o in objs:
                if o.data and o.data.users == 1 and not o.data.library and o.data.name != o.name:
                    o.data.name = o.name
        self.report({"INFO"}, f"Renamed {changed} item(s)")
        return {"FINISHED"}


class STUDIO_PT_rename(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Quick Rename"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = context.scene.studio_rename
        layout.prop(s, "scope", expand=True)
        row = layout.row(align=True)
        row.prop(s, "do_objects", toggle=True)
        row.prop(s, "do_materials", toggle=True)
        sub = row.row(align=True)
        sub.enabled = s.scope == "ALL"
        sub.prop(s, "do_collections", toggle=True)

        col = layout.column()
        col.prop(s, "strip_brackets")
        col.prop(s, "strip_numbers")
        col.prop(s, "underscores")
        col.prop(s, "title_case")
        col.prop(s, "sync_data")
        col.separator()
        col.prop(s, "find")
        col.prop(s, "replace")
        col.prop(s, "prefix")
        col.prop(s, "suffix")

        _, items = _targets(context, s)
        preview = [(i.name, new_name(i.name, s)) for i in items]
        preview = [p for p in preview if p[0] != p[1]]
        box = layout.box()
        if not preview:
            box.label(text="Nothing to change", icon="CHECKMARK")
        for old, new in preview[:6]:
            box.label(text=f"{old}  →  {new}")
        if len(preview) > 6:
            box.label(text=f"…and {len(preview) - 6} more")
        layout.operator("studio.quick_rename", icon="SORTALPHA")


class StudioRenameSettings(bpy.types.PropertyGroup):
    scope: EnumProperty(name="Scope", items=[
        ("SELECTED", "Selected", "Selected objects and their materials"),
        ("ALL", "Whole Scene", "Every object, material and collection in the scene"),
    ], default="SELECTED")
    do_objects: BoolProperty(name="Objects", default=True)
    do_materials: BoolProperty(name="Materials", default=True)
    do_collections: BoolProperty(name="Collections", default=False)
    strip_brackets: BoolProperty(name="Remove (notes) in brackets", default=True)
    strip_numbers: BoolProperty(name="Remove .001 endings", default=True)
    underscores: BoolProperty(name="Underscores to spaces", default=False)
    title_case: BoolProperty(name="Title Case", default=False)
    sync_data: BoolProperty(name="Mesh/light data follows object name", default=True)
    find: StringProperty(name="Find")
    replace: StringProperty(name="Replace")
    prefix: StringProperty(name="Prefix")
    suffix: StringProperty(name="Suffix")


classes = (StudioRenameSettings, STUDIO_OT_quick_rename, STUDIO_PT_rename)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.studio_rename = bpy.props.PointerProperty(type=StudioRenameSettings)


def unregister():
    del bpy.types.Scene.studio_rename
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
