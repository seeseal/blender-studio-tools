"""Runs inside a background Blender started by the Render Queue panel.
Usage: blender -b scene.blend --python queue_runner.py -- jobs.json progress.json
"""
import json
import os
import sys
import time

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
job_file, progress_file = args[0], args[1]
with open(job_file, "r", encoding="utf-8") as f:
    jobs = json.load(f)

state = {"jobs": [], "finished": False, "started": time.time()}
for j in jobs:
    total = 1 if j["kind"] == "STILL" else max(1, j["end"] - j["start"] + 1)
    state["jobs"].append({"name": j["name"], "status": "waiting", "done": 0, "total": total})


def write():
    tmp = progress_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, progress_file)


current = {"i": 0}


def on_frame_written(scene, *_):
    state["jobs"][current["i"]]["done"] += 1
    write()


def set_format(r, fmt):
    ims = r.image_settings
    if fmt == "MP4":
        if hasattr(ims, "media_type"):
            ims.media_type = "VIDEO"
        ims.file_format = "FFMPEG"
        r.ffmpeg.format = "MPEG4"
        r.ffmpeg.codec = "H264"
        r.ffmpeg.constant_rate_factor = "HIGH"
        r.ffmpeg.ffmpeg_preset = "GOOD"
        r.ffmpeg.audio_codec = "NONE"
        return ""
    if hasattr(ims, "media_type"):
        ims.media_type = "IMAGE"
    if fmt == "EXR":
        ims.file_format = "OPEN_EXR"
        ims.color_depth = "16"
        return ".exr"
    ims.file_format = "PNG"
    ims.color_depth = "16"
    return ".png"


bpy.app.handlers.render_write.append(on_frame_written)
write()

scene = bpy.context.scene
r = scene.render
for i, j in enumerate(jobs):
    current["i"] = i
    info = state["jobs"][i]
    cam = bpy.data.objects.get(j["camera"])
    if cam is None:
        info["status"] = "camera missing"
        write()
        continue
    info["status"] = "rendering"
    write()
    scene.camera = cam
    r.resolution_percentage = j["percent"]
    if j["samples"] > 0:
        if hasattr(scene, "cycles"):
            scene.cycles.samples = j["samples"]
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = j["samples"]
    ext = set_format(r, j["format"])
    out_dir = bpy.path.abspath(j["output"])
    os.makedirs(out_dir, exist_ok=True)
    try:
        if j["kind"] == "STILL":
            scene.frame_set(j["frame"])
            r.filepath = os.path.join(out_dir, f"{j['name']}_f{j['frame']:04d}{ext}")
            bpy.ops.render.render(write_still=True)
        else:
            scene.frame_start, scene.frame_end = j["start"], j["end"]
            r.filepath = os.path.join(out_dir, j["name"] + "_")
            bpy.ops.render.render(animation=True)
        info["done"] = info["total"]
        info["status"] = "done"
    except Exception as err:  # keep going with the next job
        info["status"] = "failed: " + str(err)[:80]
    write()

state["finished"] = True
state["elapsed"] = time.time() - state["started"]
write()
print("QUEUE FINISHED", flush=True)
