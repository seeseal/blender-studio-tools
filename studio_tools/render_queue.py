import json
import os
import subprocess
import sys
import tempfile
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)

RUNNER = os.path.join(os.path.dirname(__file__), "queue_runner.py")

# lets "//" paths (relative to the .blend) be used in Blender 4.5+
_REL = {"options": {"PATH_SUPPORTS_BLEND_RELATIVE"}} if bpy.app.version >= (4, 5, 0) else {}

# live state of the running queue (not saved in the .blend)
_STATE = {"proc": None, "progress": None, "log": None, "data": None, "started": 0.0}


def _work_dir():
    d = os.path.join(tempfile.gettempdir(), "studio_tools_queue")
    os.makedirs(d, exist_ok=True)
    return d


def _is_running():
    p = _STATE["proc"]
    return p is not None and p.poll() is None


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _poll():
    path = _STATE["progress"]
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _STATE["data"] = json.load(f)
        except (OSError, ValueError):
            pass
    _redraw()
    if _is_running():
        return 1.0
    _STATE["proc"] = None
    return None  # stop the timer


def _camera_poll(self, obj):
    return obj.type == "CAMERA"


def _fmt_time(sec):
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


# ------------------------------------------------------------------ data

class StudioRenderJob(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Use", default=True)
    name: StringProperty(name="Name", default="Shot")
    camera: PointerProperty(name="Camera", type=bpy.types.Object, poll=_camera_poll)
    kind: EnumProperty(name="Type", items=[
        ("STILL", "Still", "Single frame"),
        ("ANIM", "Animation", "Frame range"),
    ], default="STILL")
    frame: IntProperty(name="Frame", default=1)
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=250)
    percent: IntProperty(name="Resolution %", default=100, min=1, max=400, subtype="PERCENTAGE")
    samples: IntProperty(name="Samples", default=0, min=0, description="0 = use the scene's samples")
    file_format: EnumProperty(name="Format", items=[
        ("PNG", "PNG", "16-bit PNG"),
        ("EXR", "EXR", "OpenEXR"),
        ("MP4", "MP4", "H.264 video (animation only)"),
    ], default="PNG")
    output_dir: StringProperty(name="Output", subtype="DIR_PATH", default="//renders/", **_REL)


# ------------------------------------------------------------------ operators

class STUDIO_OT_job_add(bpy.types.Operator):
    bl_idname = "studio.job_add"
    bl_label = "Add Job"
    bl_description = "Add a render job for the active camera"

    def execute(self, context):
        sc = context.scene
        job = sc.studio_jobs.add()
        cam = context.active_object if context.active_object and context.active_object.type == "CAMERA" else sc.camera
        job.camera = cam
        job.name = cam.name if cam else "Shot"
        job.frame = sc.frame_current
        job.frame_start = sc.frame_start
        job.frame_end = sc.frame_end
        sc.studio_job_index = len(sc.studio_jobs) - 1
        return {"FINISHED"}


class STUDIO_OT_job_remove(bpy.types.Operator):
    bl_idname = "studio.job_remove"
    bl_label = "Remove Job"

    def execute(self, context):
        sc = context.scene
        if 0 <= sc.studio_job_index < len(sc.studio_jobs):
            sc.studio_jobs.remove(sc.studio_job_index)
            sc.studio_job_index = max(0, sc.studio_job_index - 1)
        return {"FINISHED"}


class STUDIO_OT_queue_start(bpy.types.Operator):
    bl_idname = "studio.queue_start"
    bl_label = "Start Queue"
    bl_description = "Render the ticked jobs in a separate Blender, so this one stays usable"

    def execute(self, context):
        sc = context.scene
        if _is_running():
            self.report({"WARNING"}, "A queue is already running")
            return {"CANCELLED"}
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the file first")
            return {"CANCELLED"}
        jobs = []
        for j in sc.studio_jobs:
            if not j.enabled:
                continue
            if not j.camera:
                self.report({"ERROR"}, f"Job '{j.name}' has no camera")
                return {"CANCELLED"}
            if j.kind == "STILL" and j.file_format == "MP4":
                self.report({"ERROR"}, f"Job '{j.name}': MP4 needs an animation job")
                return {"CANCELLED"}
            jobs.append({
                "name": bpy.path.clean_name(j.name) or "Shot",
                "camera": j.camera.name,
                "kind": j.kind,
                "frame": j.frame,
                "start": j.frame_start,
                "end": j.frame_end,
                "percent": j.percent,
                "samples": j.samples,
                "format": j.file_format,
                "output": j.output_dir or "//renders/",
            })
        if not jobs:
            self.report({"ERROR"}, "No ticked jobs")
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()

        work = _work_dir()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        job_file = os.path.join(work, f"jobs_{stamp}.json")
        progress = os.path.join(work, f"progress_{stamp}.json")
        log = os.path.join(work, f"log_{stamp}.txt")
        with open(job_file, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=1)

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        logf = open(log, "w", encoding="utf-8", errors="ignore")
        _STATE["proc"] = subprocess.Popen(
            [bpy.app.binary_path, "-b", bpy.data.filepath, "--python", RUNNER, "--", job_file, progress],
            stdout=logf, stderr=subprocess.STDOUT, creationflags=flags,
        )
        _STATE.update(progress=progress, log=log, data=None, started=time.time())
        if not bpy.app.timers.is_registered(_poll):
            bpy.app.timers.register(_poll, first_interval=1.0)
        self.report({"INFO"}, f"Rendering {len(jobs)} job(s) in the background")
        return {"FINISHED"}


class STUDIO_OT_queue_stop(bpy.types.Operator):
    bl_idname = "studio.queue_stop"
    bl_label = "Stop Queue"

    def execute(self, context):
        if _is_running():
            _STATE["proc"].terminate()
            self.report({"INFO"}, "Queue stopped")
        return {"FINISHED"}


class STUDIO_OT_queue_open_log(bpy.types.Operator):
    bl_idname = "studio.queue_open_log"
    bl_label = "Open Log"

    def execute(self, context):
        if _STATE["log"] and os.path.exists(_STATE["log"]):
            bpy.ops.wm.path_open(filepath=_STATE["log"])
        return {"FINISHED"}


class STUDIO_OT_open_output(bpy.types.Operator):
    bl_idname = "studio.open_output"
    bl_label = "Open Output Folder"

    def execute(self, context):
        sc = context.scene
        if 0 <= sc.studio_job_index < len(sc.studio_jobs):
            path = bpy.path.abspath(sc.studio_jobs[sc.studio_job_index].output_dir)
            os.makedirs(path, exist_ok=True)
            bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


# ------------------------------------------------------------------ UI

class STUDIO_UL_jobs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False)
        if item.kind == "STILL":
            row.label(text=f"f{item.frame}")
        else:
            row.label(text=f"{item.frame_start}-{item.frame_end}")


class STUDIO_PT_render_queue(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Render Queue"

    def draw(self, context):
        layout = self.layout
        sc = context.scene

        row = layout.row()
        row.template_list("STUDIO_UL_jobs", "", sc, "studio_jobs", sc, "studio_job_index", rows=4)
        col = row.column(align=True)
        col.operator("studio.job_add", icon="ADD", text="")
        col.operator("studio.job_remove", icon="REMOVE", text="")

        if 0 <= sc.studio_job_index < len(sc.studio_jobs):
            j = sc.studio_jobs[sc.studio_job_index]
            box = layout.box()
            box.prop(j, "camera")
            box.prop(j, "kind", expand=True)
            if j.kind == "STILL":
                box.prop(j, "frame")
            else:
                r = box.row(align=True)
                r.prop(j, "frame_start")
                r.prop(j, "frame_end")
            box.prop(j, "percent")
            box.prop(j, "samples")
            box.prop(j, "file_format")
            box.prop(j, "output_dir")
            box.operator("studio.open_output", icon="FILE_FOLDER")

        if _is_running():
            layout.operator("studio.queue_stop", icon="CANCEL")
        else:
            layout.operator("studio.queue_start", icon="RENDER_ANIMATION")

        data = _STATE["data"]
        if data:
            box = layout.box()
            total = sum(j.get("total", 0) for j in data.get("jobs", []))
            done = sum(j.get("done", 0) for j in data.get("jobs", []))
            elapsed = time.time() - _STATE["started"]
            for j in data.get("jobs", []):
                box.label(text=f"{j['name']}: {j['status']} ({j.get('done', 0)}/{j.get('total', 0)})")
            if total:
                box.progress(factor=done / total, text=f"{done}/{total} frames") if hasattr(box, "progress") \
                    else box.label(text=f"{done}/{total} frames")
            if _is_running() and done:
                left = elapsed / done * (total - done)
                box.label(text=f"Elapsed {_fmt_time(elapsed)} · about {_fmt_time(left)} left", icon="TIME")
            elif not _is_running():
                box.label(text=f"Finished in {_fmt_time(data.get('elapsed', elapsed))}" if data.get("finished")
                          else "Stopped", icon="CHECKMARK" if data.get("finished") else "ERROR")
            box.operator("studio.queue_open_log", icon="TEXT")


classes = (
    StudioRenderJob,
    STUDIO_OT_job_add,
    STUDIO_OT_job_remove,
    STUDIO_OT_queue_start,
    STUDIO_OT_queue_stop,
    STUDIO_OT_queue_open_log,
    STUDIO_OT_open_output,
    STUDIO_UL_jobs,
    STUDIO_PT_render_queue,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.studio_jobs = CollectionProperty(type=StudioRenderJob)
    bpy.types.Scene.studio_job_index = IntProperty()


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    del bpy.types.Scene.studio_job_index
    del bpy.types.Scene.studio_jobs
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
