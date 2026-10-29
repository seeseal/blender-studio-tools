"""Build the installable add-on zip into dist/.  Usage: python build.py"""
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "studio_tools")

with open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8") as f:
    version = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)

os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", f"studio_tools-{version}.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(os.listdir(SRC)):
        if name.endswith((".py", ".toml")):
            z.write(os.path.join(SRC, name), name)
print("Built", out)
