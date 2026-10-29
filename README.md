# Studio Tools for Blender

Six production tools in one add-on, built for product and commercial work.
Everything lives in the 3D Viewport sidebar (**N** → **Studio** tab).

| Tool | What it does |
|---|---|
| **Delivery Packager** | Checks a scene for hand-off problems, cleans it in one click, and saves a copy with its caches into a clean delivery folder. |
| **Render Queue** | Queue stills and animations per camera and render them in a separate background Blender, so you can keep working. |
| **Reference Matcher** | Overlays a reference image on the camera and compares your render against it, region by region. |
| **Light Follow Rig** | Makes a set of lights travel (and optionally turn) with a hero object, so the look holds through an animation. |
| **Quick Rename** | Cleans up names: removes `(notes)` and `.001` endings, find/replace, prefix/suffix, Title Case, and keeps mesh and light data names in sync. |
| **AutoComp** | Describe a look in plain words and a local AI model builds the compositor nodes for it. No API keys, nothing leaves your PC. |

Requires Blender **4.2 or newer**. Tested on Blender 5.2.

## Install

1. Download `studio_tools-1.2.0.zip` from the [`dist`](dist) folder.
2. In Blender: **Edit → Preferences → Get Extensions → ⌄ menu (top right) → Install from Disk…**, then pick the zip.
3. Open the sidebar in the 3D Viewport (**N**) and click the **Studio** tab.

To build the zip yourself from this repo: `python build.py`

## Delivery Packager

- **Check Scene** lists anything that would break or bloat a hand-off: unsaved changes, textures that aren't packed or are missing, items linked from other files, unused material slots, unconnected nodes, unused data, an absolute render path, and disk caches that need to travel with the file.
- **Clean Scene** fixes those, with a tickbox for each step: pack textures, make linked items local, remove unused material slots, remove unconnected nodes (in materials, worlds and the compositor), purge unused data, and make paths relative. Undo works.
- **Package Delivery** saves a *copy* of the scene (your working file stays open and untouched) into:

```
<Folder>/<Project>/
├─ Assets/Models/Props
├─ Assets/References
├─ Animation/Scene_Files/<Scene name>.blend   (+ its blendcache / fluid cache folders)
└─ Exports/Delivery            (+ Exports/Final_Exports if ticked)
```

Simulation caches (Dynamic Paint, cloth, particles, and Mantaflow domains inside the project folder) are copied and renamed to match the new file, so they load straight away.

## Render Queue

1. Click **+** to add a job for the active camera, and set it up: still or frame range, resolution %, samples (0 = use the scene's), PNG / EXR / MP4, and an output folder (`//renders/` means next to the .blend).
2. Tick the jobs you want and press **Start Queue**.

The file is saved first, then a second Blender renders the queue in the background using your normal preferences (so the same GPU setup). The panel shows each job's status, a progress bar and the time left. **Stop Queue** cancels it, and **Open Log** shows the render output if something fails.

## Reference Matcher

- Pick a reference image and press **Show Over Camera** to overlay it on the active camera. Use the opacity slider and the Show / In front toggles to line up framing.
- **Compare** checks your last render (F12) or a saved image against the reference on a 3×3 grid. For each region it says how much brighter or darker it is, and whether it's warmer or cooler. It also compares the overall mid-grey and contrast.

Compare works on display images (PNG, JPG, TIFF). EXR files hold linear light, so they won't line up with a JPG reference.

## Light Follow Rig

Select the lights, then **Shift-click the hero object last** so it's active, and press **Make Lights Follow**.

- **Move and turn**: the lights move and rotate with the object.
- **Move only**: the lights move with it but keep their angle.

A small empty called `<object> Light Rig` is created, and the lights are parented to it without moving. **Release Lights** unparents selected lights and keeps them where they are.

## Quick Rename

Choose **Selected** or **Whole Scene**, and whether to include objects, materials and collections. Tick the clean-up rules you want, and the panel previews the first few changes before you press **Rename**. Undo works.

## AutoComp (local AI)

Type the look you want, e.g. *"soft bloom on highlights, warm highlights with teal shadows, light vignette, fine grain"*, and press **New Comp**. A local AI model replies with a node recipe, and the add-on builds it inside a blue **AutoComp** frame at the end of your existing comp.

- **Adjust** sends the current recipe back with your new instruction (*"less bloom, cooler shadows"*) and rebuilds the frame.
- **Bypass** switches between the original image and the AutoComp result. **Remove** deletes the frame and reconnects your original chain.
- If you've run the **Reference Matcher**, tick *Use Reference Matcher numbers* and the model also gets the region-by-region brightness and colour differences, so it can grade towards the reference.
- The panel appears in the 3D Viewport sidebar and in the Compositor's sidebar (**N → Studio**).

**Safe by design.** The model never writes code. It can only reply with a recipe (node types, settings and links), and the add-on checks every node, setting and socket against the compositor nodes in *your* Blender before building anything. Anything it doesn't recognise is skipped and listed under *Notes*.

**Setup (one time, free):**

1. Install [LM Studio](https://lmstudio.ai), download a model (Qwen 3/3.5 instruct models work well; a 7B+ model is the practical minimum), and load it with a context length of **8192 or more**.
2. In LM Studio's **Developer** tab, start the server (default `http://localhost:1234`).
3. In Blender: **Preferences → System → Network → Allow Online Access** must be on (the add-on only calls your own computer).
4. **Preferences → Add-ons → Studio Tools** → **Test Connection**. Leave *Model* empty to use whichever model is loaded.

Ollama works too: set the server to `http://localhost:11434/v1`.

Local models vary: smaller ones sometimes pick odd values, which is why everything lands in one frame you can bypass, adjust or remove. Generation time depends on your model and GPU (tens of seconds is normal). Blender stays usable while it thinks.

## License

GPL-3.0-or-later, the same as Blender add-ons in general. See [LICENSE](LICENSE).
