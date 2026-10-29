import os
import tempfile

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       StringProperty)

_REL = {"options": {"PATH_SUPPORTS_BLEND_RELATIVE"}} if bpy.app.version >= (4, 5, 0) else {}
_CELLS = ["Top left", "Top", "Top right", "Left", "Centre", "Right", "Bottom left", "Bottom", "Bottom right"]


def _overlay(context):
    s = context.scene.studio_ref
    cam = context.scene.camera
    if not cam or not s.image_name:
        return None
    for bg in cam.data.background_images:
        if bg.image and bg.image.name == s.image_name:
            return bg
    return None


def _update_overlay(self, context):
    bg = _overlay(context)
    if bg:
        bg.alpha = self.opacity
        bg.show_background_image = self.show
        bg.display_depth = "FRONT" if self.in_front else "BACK"


def _grid_stats(img, size=240):
    """3x3 grid of average display colours (0-255) plus brightness percentiles."""
    import numpy as np
    tmp = img.copy()
    try:
        w, h = tmp.size
        if w == 0 or h == 0:
            raise RuntimeError("Image has no pixels")
        tmp.scale(size, max(1, int(size * h / w)))
        w, h = tmp.size
        px = np.empty(w * h * 4, dtype=np.float32)
        tmp.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(tmp)
    px = px.reshape(h, w, 4)[::-1, :, :3] * 255.0  # flip so row 0 is the top
    cells = []
    for r in range(3):
        for c in range(3):
            block = px[r * h // 3:(r + 1) * h // 3, c * w // 3:(c + 1) * w // 3]
            cells.append(block.reshape(-1, 3).mean(axis=0))
    luma = px @ np.array([0.2126, 0.7152, 0.0722])
    return cells, np.percentile(luma, [5, 50, 95])


def _load_raw(path):
    """Load an image so its pixels are the values stored in the file (as a viewer shows them).
    Without this, 16-bit PNGs and EXRs come in as linear light and read far too dark."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.colorspace_settings.name = "Non-Color"
    except TypeError:
        pass
    return img


def _luma(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


class STUDIO_OT_ref_load(bpy.types.Operator):
    bl_idname = "studio.ref_load"
    bl_label = "Show Over Camera"
    bl_description = "Overlay the reference on the active camera so you can line up framing"

    def execute(self, context):
        s = context.scene.studio_ref
        path = bpy.path.abspath(s.image_path)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a reference image first")
            return {"CANCELLED"}
        cam = context.scene.camera
        if not cam:
            self.report({"ERROR"}, "The scene has no active camera")
            return {"CANCELLED"}
        img = bpy.data.images.load(path, check_existing=True)
        bg = _overlay(context)
        if bg is None:
            bg = cam.data.background_images.new()
        bg.image = img
        bg.frame_method = "FIT"
        cam.data.show_background_images = True
        s.image_name = img.name
        _update_overlay(s, context)
        return {"FINISHED"}


class STUDIO_OT_ref_remove(bpy.types.Operator):
    bl_idname = "studio.ref_remove"
    bl_label = "Remove Overlay"

    def execute(self, context):
        bg = _overlay(context)
        if bg:
            context.scene.camera.data.background_images.remove(bg)
        return {"FINISHED"}


class STUDIO_OT_ref_compare(bpy.types.Operator):
    bl_idname = "studio.ref_compare"
    bl_label = "Compare"
    bl_description = "Compare brightness and colour of your render against the reference, region by region"

    def execute(self, context):
        s = context.scene.studio_ref
        wm = context.window_manager
        ref_path = bpy.path.abspath(s.image_path)
        if not os.path.isfile(ref_path):
            self.report({"ERROR"}, "Pick a reference image first")
            return {"CANCELLED"}

        cleanup = []
        if s.source == "RENDER":
            rr = bpy.data.images.get("Render Result")
            if rr is None:
                self.report({"ERROR"}, "Render a frame first (F12)")
                return {"CANCELLED"}
            tmp_path = os.path.join(tempfile.gettempdir(), "studio_ref_render.png")
            ims = context.scene.render.image_settings
            keep = (getattr(ims, "media_type", None), ims.file_format, ims.color_depth)
            try:
                # save as an 8-bit PNG whatever the scene's output format is (e.g. MP4)
                if keep[0] is not None:
                    ims.media_type = "IMAGE"
                ims.file_format = "PNG"
                ims.color_depth = "8"
                rr.save_render(tmp_path, scene=context.scene)
            except RuntimeError:
                self.report({"ERROR"}, "Render a frame first (F12)")
                return {"CANCELLED"}
            finally:
                if keep[0] is not None:
                    ims.media_type = keep[0]
                ims.file_format = keep[1]
                if ims.file_format == "PNG":
                    ims.color_depth = keep[2]
            ours = _load_raw(tmp_path)
            cleanup.append(ours)
        else:
            path = bpy.path.abspath(s.compare_path)
            if not os.path.isfile(path):
                self.report({"ERROR"}, "Pick the render image to compare")
                return {"CANCELLED"}
            ours = _load_raw(path)
            cleanup.append(ours)
        ref = _load_raw(ref_path)
        cleanup.append(ref)
        try:
            ref_cells, ref_p = _grid_stats(ref)
            our_cells, our_p = _grid_stats(ours)
        finally:
            for img in cleanup:
                bpy.data.images.remove(img)

        wm.studio_ref_rows.clear()
        for name, a, b in zip(_CELLS, ref_cells, our_cells):
            row = wm.studio_ref_rows.add()
            la, lb = _luma(a), _luma(b)
            diff = (lb - la) / max(la, 1.0) * 100.0
            warm = (b[0] - b[2]) - (a[0] - a[2])
            hints = []
            if abs(diff) >= 5:
                hints.append("brighter" if diff > 0 else "darker")
            if abs(warm) >= 8:
                hints.append("warmer" if warm > 0 else "cooler")
            row.text = f"{name}: {diff:+.0f}%  {' & '.join(hints) if hints else 'close'}"
            row.ok = not hints
        ref_con, our_con = ref_p[2] - ref_p[0], our_p[2] - our_p[0]
        s.summary = (f"Mid-grey {our_p[1]:.0f} vs {ref_p[1]:.0f} · contrast {our_con:.0f} vs {ref_con:.0f}"
                     f" ({'more' if our_con > ref_con * 1.05 else 'less' if our_con < ref_con * 0.95 else 'same'})")
        return {"FINISHED"}


class STUDIO_PT_reference(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Reference Matcher"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = context.scene.studio_ref
        wm = context.window_manager
        layout.prop(s, "image_path")
        row = layout.row(align=True)
        row.operator("studio.ref_load", icon="IMAGE_REFERENCE")
        row.operator("studio.ref_remove", icon="X", text="")
        if _overlay(context):
            col = layout.column(align=True)
            col.prop(s, "opacity", slider=True)
            r = col.row(align=True)
            r.prop(s, "show", toggle=True)
            r.prop(s, "in_front", toggle=True)

        layout.separator()
        layout.prop(s, "source", expand=True)
        if s.source == "FILE":
            layout.prop(s, "compare_path")
        layout.operator("studio.ref_compare", icon="SEQ_HISTOGRAM")
        if len(wm.studio_ref_rows):
            box = layout.box()
            for row in wm.studio_ref_rows:
                box.label(text=row.text, icon="CHECKMARK" if row.ok else "DOT")
            if s.summary:
                box.label(text=s.summary)


class StudioRefRow(bpy.types.PropertyGroup):
    text: StringProperty()
    ok: BoolProperty()


class StudioRefSettings(bpy.types.PropertyGroup):
    image_path: StringProperty(name="Reference", subtype="FILE_PATH", **_REL)
    image_name: StringProperty()
    opacity: FloatProperty(name="Opacity", default=0.5, min=0.0, max=1.0, update=_update_overlay)
    show: BoolProperty(name="Show", default=True, update=_update_overlay)
    in_front: BoolProperty(name="In front", default=True, update=_update_overlay)
    source: EnumProperty(name="Compare", items=[
        ("RENDER", "Last render", "The image in the Render window (F12)"),
        ("FILE", "Image file", "A render saved to disk"),
    ], default="RENDER")
    compare_path: StringProperty(name="Render", subtype="FILE_PATH", **_REL)
    summary: StringProperty()


classes = (StudioRefRow, StudioRefSettings, STUDIO_OT_ref_load, STUDIO_OT_ref_remove,
           STUDIO_OT_ref_compare, STUDIO_PT_reference)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.studio_ref = bpy.props.PointerProperty(type=StudioRefSettings)
    bpy.types.WindowManager.studio_ref_rows = CollectionProperty(type=StudioRefRow)


def unregister():
    del bpy.types.WindowManager.studio_ref_rows
    del bpy.types.Scene.studio_ref
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
