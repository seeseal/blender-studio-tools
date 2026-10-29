"""AutoComp: describe a look in plain words, a local AI model (LM Studio / Ollama) answers with a
compositor node recipe, and the add-on builds it inside a frame. No API keys, nothing leaves the PC.
The model never writes code: its JSON is checked against the nodes that exist in this Blender."""
import json
import re
import threading
import time
import urllib.error
import urllib.request

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

PKG = __package__
TAG = "studio_autocomp"

WHITELIST = [
    "CompositorNodeGlare", "CompositorNodeBlur", "CompositorNodeBilateralblur", "CompositorNodeDefocus",
    "CompositorNodeColorBalance", "CompositorNodeColorCorrection", "CompositorNodeHueSat",
    "CompositorNodeBrightContrast", "CompositorNodeGamma", "CompositorNodeExposure", "CompositorNodeTonemap",
    "CompositorNodeValToRGB", "CompositorNodeRGBToBW", "CompositorNodeInvert", "CompositorNodeEllipseMask",
    "CompositorNodeBoxMask", "CompositorNodeLensdist", "CompositorNodeFilter", "CompositorNodeDilateErode",
    "CompositorNodeSunBeams", "CompositorNodeKuwahara", "CompositorNodePixelate", "CompositorNodeAlphaOver",
    "CompositorNodeMixRGB", "ShaderNodeMix", "CompositorNodeMath", "ShaderNodeMath",
    "ShaderNodeTexWhiteNoise", "CompositorNodeSceneTime", "CompositorNodeSeparateColor",
    "CompositorNodeCombineColor", "CompositorNodeImageCoordinates",
]
_SKIP_PROPS = {"rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions",
               "parent", "select", "show_options", "show_preview", "hide", "mute", "show_texture", "type",
               "inputs", "outputs", "internal_links", "color", "use_custom_color", "bl_idname", "bl_label",
               "bl_description", "bl_icon", "bl_static_type", "bl_width_default", "bl_width_min",
               "bl_width_max", "bl_height_default", "bl_height_min", "bl_height_max", "warning_propagation",
               "color_tag", "is_active_output"}

_JOB = {"thread": None, "result": None, "error": None, "started": 0.0, "raw": "", "prompt": "", "edit": False}
_CATALOG = {"text": None, "info": None}


# ------------------------------------------------------------------ compositor access

def comp_tree(scene, create=True):
    """Return (tree, output_socket) for this Blender version, creating a basic comp if needed."""
    if hasattr(scene, "compositing_node_group"):  # Blender 5.x
        tree = scene.compositing_node_group
        if tree is None and create:
            tree = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
            tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            rl = tree.nodes.new("CompositorNodeRLayers")
            out = tree.nodes.new("NodeGroupOutput")
            rl.location, out.location = (-300, 0), (300, 0)
            tree.links.new(rl.outputs["Image"], out.inputs[0])
            scene.compositing_node_group = tree
        if tree is None:
            return None, None
        out = next((n for n in tree.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output), None) \
            or next((n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"), None)
        return tree, (out.inputs[0] if out and out.inputs else None)
    if create:  # Blender 4.x
        scene.use_nodes = True
    tree = scene.node_tree
    if tree is None:
        return None, None
    out = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeComposite"), None)
    if out is None and create:
        out = tree.nodes.new("CompositorNodeComposite")
    return tree, (out.inputs["Image"] if out else None)


def render_layers(tree):
    rl = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"), None)
    if rl is None:
        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.location = (-600, 0)
    return rl


def autocomp_frames(tree):
    return [n for n in tree.nodes if n.bl_idname == "NodeFrame" and n.get(TAG)]


# ------------------------------------------------------------------ catalogue for the model

def _socket_kind(s):
    t = s.type
    if t == "RGBA":
        return "color"
    if t == "VALUE":
        return "float"
    if t == "MENU":
        return "menu(" + "|".join(_menu_items(s)) + ")"
    return t.lower()


def _menu_items(s):
    """Menu sockets don't expose their items, but Blender lists them when given a wrong value.
    Only ever called on the throw-away probe node."""
    keep = s.default_value
    try:
        s.default_value = "__studio_probe__"
    except TypeError as err:
        found = re.findall(r"'([^']+)'", str(err).split("not found in", 1)[-1])
        if found:
            return found
    finally:
        try:
            s.default_value = keep
        except TypeError:
            pass
    return [str(keep)]


def _socket_label(node, s):
    """Use the unique identifier when several inputs share a name (e.g. Master/Shadows Saturation)."""
    same = sum(1 for o in node.inputs if o.enabled and not o.hide and o.name == s.name)
    return s.identifier if same > 1 else s.name


def build_catalog():
    if _CATALOG["text"]:
        return _CATALOG["text"], _CATALOG["info"]
    probe = bpy.data.node_groups.new("__studio_probe__", "CompositorNodeTree")
    lines, info = [], {}
    try:
        for t in WHITELIST:
            try:
                n = probe.nodes.new(t)
            except RuntimeError:
                continue
            props = []
            for p in n.bl_rna.properties:
                if p.is_readonly or p.identifier in _SKIP_PROPS or p.identifier.startswith("bl_"):
                    continue
                if p.type == "ENUM" and not p.is_enum_flag:
                    items = [i.identifier for i in p.enum_items]
                    if items:
                        props.append(f"{p.identifier}=[{'|'.join(items)}]")
                elif p.type in {"BOOLEAN", "FLOAT", "INT"} and getattr(p, "array_length", 0) == 0:
                    props.append(f"{p.identifier}:{p.type.lower()}")
            ins = [f"{_socket_label(n, s)}:{_socket_kind(s)}" for s in n.inputs if s.enabled and not s.hide]
            outs = [s.name for s in n.outputs if s.enabled and not s.hide]
            info[t] = True
            line = f"- {t} ({n.bl_label}) inputs[{', '.join(ins)}] outputs[{', '.join(outs)}]"
            if props:
                line += f" props[{', '.join(props[:12])}]"
            lines.append(line)
    finally:
        bpy.data.node_groups.remove(probe)
    _CATALOG["text"], _CATALOG["info"] = "\n".join(lines), info
    return _CATALOG["text"], info


SYSTEM = """You are a senior compositor building Blender compositor node setups.
Reply with ONE JSON object and nothing else. Schema:
{"summary": "one short sentence",
 "nodes": [{"id": "short_id", "type": "<node type from the list>", "label": "Readable Name",
            "props": {"<prop>": value}, "inputs": {"<input socket name>": value}}],
 "links": [{"from": "<id|INPUT|PASS:<pass name>>", "from_socket": "<output name, optional>",
            "to": "<id|OUTPUT>", "to_socket": "<input name, optional>"}]}
Rules:
- INPUT is the image currently going to the output. OUTPUT is the final image. The chain must go INPUT -> ... -> OUTPUT.
- Use only node types, props and socket names from the list, spelled exactly. Menu inputs take one of the listed words. Colours are [r,g,b,a] floats 0-1.
- Set "props" (node settings such as data_type/blend_type/filter_type) before relying on sockets they enable.
- For colour mixing with ShaderNodeMix set props {"data_type":"RGBA","blend_type":"..."}; its colour sockets are named A and B, the amount is Factor.
- Keep it subtle and production-grade: few nodes, sensible values, no duplicate effects."""


def _scene_context(context, edit_recipe):
    sc = context.scene
    tree, _ = comp_tree(sc, create=False)
    passes = []
    if tree:
        rl = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"), None)
        if rl:
            passes = [s.name for s in rl.outputs if s.enabled]
    parts = [
        f"Render: {sc.render.resolution_x}x{sc.render.resolution_y}, view transform {sc.view_settings.view_transform}"
        f" / look {sc.view_settings.look}, exposure {sc.view_settings.exposure:.2f}.",
        f"Available render passes: {', '.join(passes) if passes else 'Image'}.",
    ]
    s = sc.studio_autocomp
    rows = getattr(context.window_manager, "studio_ref_rows", None)
    if s.use_reference and rows and len(rows):
        parts.append("Measured against the reference image (positive = our render is brighter):")
        parts += ["  " + r.text for r in rows]
        if getattr(sc, "studio_ref", None) and sc.studio_ref.summary:
            parts.append("  " + sc.studio_ref.summary)
    if edit_recipe:
        parts.append("Current AutoComp recipe to modify (return the full updated recipe):")
        parts.append(json.dumps(edit_recipe))
    return "\n".join(parts)


# ------------------------------------------------------------------ talking to the local server

def _prefs():
    return bpy.context.preferences.addons[PKG].preferences


def _http(url, payload=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def list_models(base):
    data = _http(base.rstrip("/") + "/models", timeout=5)
    return [m.get("id") for m in data.get("data", []) if m.get("id")]


def _worker(base, model, system, user, timeout):
    try:
        if not model:
            models = list_models(base)
            if not models:
                raise RuntimeError("The AI server has no model loaded")
            model = models[0]
        payload = {"model": model, "temperature": 0.2, "max_tokens": 2500, "stream": False,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user + "\n/no_think"}]}
        data = _http(base.rstrip("/") + "/chat/completions", payload, timeout=timeout)
        _JOB["raw"] = data["choices"][0]["message"].get("content") or ""
        _JOB["result"] = parse_recipe(_JOB["raw"])
    except urllib.error.URLError as err:
        _JOB["error"] = f"Can't reach the AI server at {base} ({getattr(err, 'reason', err)}). Start LM Studio's server."
    except Exception as err:  # shown in the panel
        _JOB["error"] = str(err)[:300]


def parse_recipe(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model didn't return a recipe")
    chunk = text[start:end + 1]
    try:
        recipe = json.loads(chunk)
    except json.JSONDecodeError:
        recipe = json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))  # trailing commas
    if not isinstance(recipe.get("nodes"), list) or not recipe["nodes"]:
        raise ValueError("The recipe has no nodes")
    recipe.setdefault("links", [])
    return recipe


# ------------------------------------------------------------------ building

def _set_value(target, value):
    """Assign value to a socket default_value or return False."""
    try:
        cur = target.default_value
        if hasattr(cur, "__len__") and not isinstance(cur, str):
            vals = list(value) if isinstance(value, (list, tuple)) else [value] * len(cur)
            vals = (vals + [1.0] * len(cur))[:len(cur)]
            target.default_value = vals
        else:
            target.default_value = value
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _find_socket(sockets, name, prefer_image=True):
    live = [s for s in sockets if s.enabled and not s.hide]
    if name:
        low = name.lower()
        for s in live:
            if s.identifier == name:
                return s
        for s in live:
            if s.name == name:
                return s
        for s in live:
            if s.name.lower() == low:
                return s
    if prefer_image:
        for s in live:
            if s.name in {"Image", "Color", "A"} and s.type == "RGBA":
                return s
    return live[0] if live else None


def remove_autocomp(tree, out_socket):
    for frame in autocomp_frames(tree):
        src_node = tree.nodes.get(frame.get("src_node", ""))
        if src_node and out_socket:
            src = src_node.outputs.get(frame.get("src_socket", "Image")) or src_node.outputs[0]
            tree.links.new(src, out_socket)
        for n in [n for n in tree.nodes if n.parent == frame]:
            tree.nodes.remove(n)
        tree.nodes.remove(frame)


def build_recipe(context, recipe, prompt):
    sc = context.scene
    tree, out_socket = comp_tree(sc)
    if out_socket is None:
        raise RuntimeError("No compositor output found")
    _, valid = build_catalog()
    warnings = []

    # restore the original chain if we're replacing an earlier AutoComp
    remove_autocomp(tree, out_socket)
    if out_socket.is_linked:
        src = out_socket.links[0].from_socket
    else:
        src = render_layers(tree).outputs["Image"]
    rl = render_layers(tree)

    frame = tree.nodes.new("NodeFrame")
    frame.label = "AutoComp: " + (prompt[:40] + ("…" if len(prompt) > 40 else ""))
    frame[TAG] = True
    frame["recipe"] = json.dumps(recipe)
    frame["prompt"] = prompt
    frame["src_node"], frame["src_socket"] = src.node.name, src.name
    frame.use_custom_color = True
    frame.color = (0.25, 0.32, 0.45)

    made = {}
    for spec in recipe["nodes"]:
        t, nid = spec.get("type"), str(spec.get("id", ""))
        if t not in valid or not nid:
            warnings.append(f"skipped unknown node {t}")
            continue
        node = tree.nodes.new(t)
        node.parent = frame
        node.label = spec.get("label", "")[:60]
        for k, v in (spec.get("props") or {}).items():
            if k in _SKIP_PROPS or k.startswith("bl_") or not hasattr(node, k):
                warnings.append(f"{nid}: no setting '{k}'")
                continue
            try:
                setattr(node, k, v)
            except (TypeError, ValueError, AttributeError):
                warnings.append(f"{nid}: bad value for '{k}'")
        for k, v in (spec.get("inputs") or {}).items():
            sock = _find_socket(node.inputs, k, prefer_image=False)
            if sock is None or str(k).lower() not in {sock.name.lower(), sock.identifier.lower()}:
                # settings that are node properties in older Blender
                if hasattr(node, k) and k not in _SKIP_PROPS:
                    try:
                        setattr(node, k, v)
                        continue
                    except (TypeError, ValueError, AttributeError):
                        pass
                warnings.append(f"{nid}: no input '{k}'")
                continue
            if not _set_value(sock, v):
                warnings.append(f"{nid}: bad value for '{k}'")
        made[nid] = node

    def out_of(ref, sock_name):
        if ref == "INPUT":
            return src
        if isinstance(ref, str) and ref.startswith("PASS:"):
            return rl.outputs.get(ref[5:]) if rl.outputs.get(ref[5:]) and rl.outputs[ref[5:]].enabled else None
        node = made.get(ref)
        return _find_socket(node.outputs, sock_name) if node else None

    def in_of(ref, sock_name):
        if ref == "OUTPUT":
            return out_socket
        node = made.get(ref)
        if not node:
            return None
        if sock_name:
            return _find_socket(node.inputs, sock_name, prefer_image=False)
        free = [s for s in node.inputs if s.enabled and not s.hide and not s.is_linked]
        return _find_socket(free, None) if free else None

    linked_out = False
    for ln in recipe["links"]:
        a = out_of(ln.get("from"), ln.get("from_socket"))
        b = in_of(ln.get("to"), ln.get("to_socket"))
        if a is None or b is None:
            warnings.append(f"skipped link {ln.get('from')} -> {ln.get('to')}")
            continue
        tree.links.new(a, b)
        linked_out = linked_out or b == out_socket
    if not linked_out:
        # fall back: chain through whatever ends last
        last = next((made[n["id"]] for n in reversed(recipe["nodes"]) if n.get("id") in made), None)
        if last:
            tree.links.new(_find_socket(last.outputs, None), out_socket)
            warnings.append("connected the last node to the output")
        else:
            tree.links.new(src, out_socket)

    # layout: columns by distance from the source
    depth = {}

    def walk(node, d=0, seen=()):
        if node in seen:
            return
        depth[node] = max(depth.get(node, 0), d)
        for o in node.outputs:
            for lk in o.links:
                if lk.to_node in made.values():
                    walk(lk.to_node, d + 1, seen + (node,))
    for n in made.values():
        if not any(lk.from_node in made.values() for s in n.inputs for lk in s.links):
            walk(n, 1)
    x0, y0 = src.node.location.x + src.node.width + 80, src.node.location.y
    rows = {}
    for n in made.values():
        d = depth.get(n, 1)
        i = rows.get(d, 0)
        rows[d] = i + 1
        n.location = (x0 + (d - 1) * 240, y0 - i * 220)
    out_node = out_socket.node
    right = max((n.location.x + n.width for n in made.values()), default=x0)
    if out_node.location.x < right + 80:
        out_node.location.x = right + 120
    frame["last_node"] = next((lk.from_node.name for lk in out_socket.links), "")
    return warnings


# ------------------------------------------------------------------ operators

def _poll_job():
    t = _JOB["thread"]
    _redraw()
    if t and t.is_alive():
        return 0.5
    _JOB["thread"] = None
    if _JOB["result"] is not None:
        try:
            warns = build_recipe(bpy.context, _JOB["result"], _JOB["prompt"])
            s = bpy.context.scene.studio_autocomp
            s.status = _JOB["result"].get("summary", "Done")[:200]
            s.warnings = "; ".join(warns)[:400]
        except Exception as err:
            bpy.context.scene.studio_autocomp.status = "Build failed: " + str(err)[:200]
    elif _JOB["error"]:
        bpy.context.scene.studio_autocomp.status = _JOB["error"]
    _redraw()
    return None


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in {"VIEW_3D", "NODE_EDITOR"}:
                area.tag_redraw()


class STUDIO_OT_autocomp(bpy.types.Operator):
    bl_idname = "studio.autocomp"
    bl_label = "Build Comp"
    bl_description = "Ask the local AI model for a compositor setup and build it in a frame"

    edit: BoolProperty(default=False)

    def execute(self, context):
        s = context.scene.studio_autocomp
        if _JOB["thread"]:
            self.report({"WARNING"}, "Still working on the last request")
            return {"CANCELLED"}
        if not s.prompt.strip():
            self.report({"ERROR"}, "Describe the look you want")
            return {"CANCELLED"}
        if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
            self.report({"ERROR"}, "Turn on Preferences > System > Network > Allow Online Access")
            return {"CANCELLED"}
        edit_recipe = None
        if self.edit:
            tree, _ = comp_tree(context.scene, create=False)
            frames = autocomp_frames(tree) if tree else []
            if frames:
                edit_recipe = json.loads(frames[0]["recipe"])
        catalog, _ = build_catalog()
        system = SYSTEM + "\n\nAvailable nodes:\n" + catalog
        user = f"Request: {s.prompt.strip()}\n\n{_scene_context(context, edit_recipe)}"
        p = _prefs()
        _JOB.update(result=None, error=None, raw="", started=time.time(), prompt=s.prompt.strip())
        _JOB["thread"] = threading.Thread(target=_worker, args=(p.server_url, p.model.strip(), system, user,
                                                                p.timeout), daemon=True)
        _JOB["thread"].start()
        s.status = "Thinking…"
        s.warnings = ""
        if not bpy.app.timers.is_registered(_poll_job):
            bpy.app.timers.register(_poll_job, first_interval=0.5)
        return {"FINISHED"}


class STUDIO_OT_autocomp_toggle(bpy.types.Operator):
    bl_idname = "studio.autocomp_toggle"
    bl_label = "Bypass"
    bl_description = "Switch between the AutoComp result and the original image"

    def execute(self, context):
        tree, out_socket = comp_tree(context.scene, create=False)
        frames = autocomp_frames(tree) if tree else []
        if not frames or out_socket is None:
            return {"CANCELLED"}
        f = frames[0]
        src_node = tree.nodes.get(f["src_node"])
        last = tree.nodes.get(f.get("last_node", ""))
        if not src_node:
            return {"CANCELLED"}
        src = src_node.outputs.get(f["src_socket"]) or src_node.outputs[0]
        feeding = out_socket.links[0].from_node if out_socket.is_linked else None
        if feeding is not None and feeding.parent == f:
            tree.links.new(src, out_socket)
        elif last:
            tree.links.new(_find_socket(last.outputs, None), out_socket)
        return {"FINISHED"}


class STUDIO_OT_autocomp_remove(bpy.types.Operator):
    bl_idname = "studio.autocomp_remove"
    bl_label = "Remove AutoComp"
    bl_description = "Delete the AutoComp frame and reconnect the original image"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree, out_socket = comp_tree(context.scene, create=False)
        if tree:
            remove_autocomp(tree, out_socket)
        context.scene.studio_autocomp.status = ""
        return {"FINISHED"}


class STUDIO_OT_autocomp_test(bpy.types.Operator):
    bl_idname = "studio.autocomp_test"
    bl_label = "Test Connection"

    def execute(self, context):
        p = _prefs()
        try:
            models = list_models(p.server_url)
        except Exception as err:
            self.report({"ERROR"}, f"No AI server at {p.server_url}: {err}")
            return {"CANCELLED"}
        if not models:
            self.report({"WARNING"}, "Server is running but no model is loaded")
            return {"FINISHED"}
        if not p.model:
            p.model = models[0]
        self.report({"INFO"}, "Models: " + ", ".join(models[:5]))
        return {"FINISHED"}


class STUDIO_OT_autocomp_copy_reply(bpy.types.Operator):
    bl_idname = "studio.autocomp_copy_reply"
    bl_label = "Copy AI Reply"
    bl_description = "Copy the model's last raw reply to the clipboard"

    def execute(self, context):
        context.window_manager.clipboard = _JOB["raw"] or ""
        return {"FINISHED"}


# ------------------------------------------------------------------ UI

def _draw(layout, context):
    s = context.scene.studio_autocomp
    layout.prop(s, "prompt", text="")
    rows = getattr(context.window_manager, "studio_ref_rows", None)
    if rows is not None and len(rows):
        layout.prop(s, "use_reference")
    tree, _ = comp_tree(context.scene, create=False)
    has = bool(tree and autocomp_frames(tree))
    row = layout.row(align=True)
    busy = _JOB["thread"] is not None
    row.enabled = not busy
    row.operator("studio.autocomp", text="New Comp", icon="NODETREE").edit = False
    sub = row.row(align=True)
    sub.enabled = has and not busy
    sub.operator("studio.autocomp", text="Adjust", icon="MODIFIER").edit = True
    if busy:
        layout.label(text=f"Thinking… {int(time.time() - _JOB['started'])}s", icon="SORTTIME")
    elif s.status:
        box = layout.box()
        for chunk in _wrap(s.status, 42):
            box.label(text=chunk)
        if s.warnings:
            for chunk in _wrap("Notes: " + s.warnings, 42)[:4]:
                box.label(text=chunk, icon="INFO")
        if _JOB["raw"]:
            box.operator("studio.autocomp_copy_reply", icon="COPYDOWN")
    if has:
        row = layout.row(align=True)
        row.operator("studio.autocomp_toggle", icon="HIDE_OFF")
        row.operator("studio.autocomp_remove", icon="TRASH", text="Remove")


def _wrap(text, n):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


class STUDIO_PT_autocomp(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "AutoComp (local AI)"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw(self.layout, context)


class STUDIO_PT_autocomp_nodes(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "AutoComp (local AI)"

    @classmethod
    def poll(cls, context):
        return context.space_data and context.space_data.tree_type == "CompositorNodeTree"

    def draw(self, context):
        _draw(self.layout, context)


class StudioAutoCompSettings(bpy.types.PropertyGroup):
    prompt: StringProperty(name="Look", description="Describe the look, e.g. 'soft bloom, warm highlights, "
                                                    "teal shadows, light vignette, fine film grain'")
    use_reference: BoolProperty(name="Use Reference Matcher numbers", default=True)
    status: StringProperty()
    warnings: StringProperty()


class StudioToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = PKG

    server_url: StringProperty(name="AI server", default="http://localhost:1234/v1",
                               description="OpenAI-compatible server. LM Studio: http://localhost:1234/v1 · "
                                           "Ollama: http://localhost:11434/v1")
    model: StringProperty(name="Model", description="Leave empty to use the first loaded model")
    timeout: IntProperty(name="Timeout (s)", default=240, min=10, max=1800)

    def draw(self, context):
        col = self.layout.column()
        col.label(text="AutoComp uses a local AI server, no API key needed.")
        col.prop(self, "server_url")
        col.prop(self, "model")
        col.prop(self, "timeout")
        col.operator("studio.autocomp_test", icon="LINKED")


classes = (StudioAutoCompSettings, StudioToolsPreferences, STUDIO_OT_autocomp, STUDIO_OT_autocomp_toggle,
           STUDIO_OT_autocomp_remove, STUDIO_OT_autocomp_test, STUDIO_OT_autocomp_copy_reply,
           STUDIO_PT_autocomp, STUDIO_PT_autocomp_nodes)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.studio_autocomp = bpy.props.PointerProperty(type=StudioAutoCompSettings)


def unregister():
    if bpy.app.timers.is_registered(_poll_job):
        bpy.app.timers.unregister(_poll_job)
    del bpy.types.Scene.studio_autocomp
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
