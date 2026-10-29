import bpy
from bpy.props import EnumProperty


def _selected_lights(context, target):
    return [o for o in context.selected_objects if o.type == "LIGHT" and o != target]


class STUDIO_OT_light_rig(bpy.types.Operator):
    bl_idname = "studio.light_rig"
    bl_label = "Make Lights Follow"
    bl_description = ("Select the lights, then the hero object last (active). "
                      "The lights will travel with it and keep today's look on every frame")
    bl_options = {"REGISTER", "UNDO"}

    follow: EnumProperty(name="Follow", items=[
        ("LOCATION", "Move only", "Lights move with the object but don't turn with it"),
        ("FULL", "Move and turn", "Lights move and rotate with the object"),
    ], default="FULL")

    @classmethod
    def poll(cls, context):
        t = context.active_object
        return t is not None and t.type != "LIGHT" and context.mode == "OBJECT"

    def execute(self, context):
        target = context.active_object
        lights = _selected_lights(context, target)
        if not lights:
            self.report({"ERROR"}, "Select some lights too (hero object last)")
            return {"CANCELLED"}

        rig = bpy.data.objects.new(f"{target.name} Light Rig", None)
        rig.empty_display_type = "PLAIN_AXES"
        rig.empty_display_size = max(target.dimensions) * 0.6 if max(target.dimensions) > 0 else 0.1
        coll = lights[0].users_collection[0] if lights[0].users_collection else context.collection
        coll.objects.link(rig)

        context.view_layer.update()
        if self.follow == "FULL":
            con = rig.constraints.new("CHILD_OF")
            con.target = target
            con.inverse_matrix = target.matrix_world.inverted()
        else:
            con = rig.constraints.new("COPY_LOCATION")
            con.target = target
            con.use_offset = True
            rig.location = -target.matrix_world.translation
        con.name = "Follow " + target.name
        context.view_layer.update()

        # rig sits at identity on the current frame, so parenting keeps every light where it is
        inv = rig.matrix_world.inverted()
        for light in lights:
            mw = light.matrix_world.copy()
            light.parent = rig
            light.matrix_parent_inverse = inv
            light.matrix_world = mw
        self.report({"INFO"}, f"{len(lights)} light(s) now follow {target.name}")
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class STUDIO_OT_light_unrig(bpy.types.Operator):
    bl_idname = "studio.light_unrig"
    bl_label = "Release Lights"
    bl_description = "Unparent the selected lights and keep them where they are now"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.type == "LIGHT" and o.parent for o in context.selected_objects)

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            if o.type == "LIGHT" and o.parent:
                mw = o.matrix_world.copy()
                o.parent = None
                o.matrix_world = mw
                n += 1
        self.report({"INFO"}, f"Released {n} light(s)")
        return {"FINISHED"}


class STUDIO_PT_light_rig(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Light Follow Rig"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        t = context.active_object
        lights = _selected_lights(context, t) if t else []
        col = layout.column()
        col.label(text="1. Select the lights", icon="LIGHT")
        col.label(text="2. Shift-click the hero object last", icon="OBJECT_DATA")
        box = layout.box()
        box.label(text=f"Hero: {t.name if t and t.type != 'LIGHT' else '—'}")
        box.label(text=f"Lights selected: {len(lights)}")
        layout.operator("studio.light_rig", icon="CON_CHILDOF")
        layout.operator("studio.light_unrig", icon="UNLINKED")


classes = (STUDIO_OT_light_rig, STUDIO_OT_light_unrig, STUDIO_PT_light_rig)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
