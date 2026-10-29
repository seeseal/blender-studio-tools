"""Studio Tools: delivery packager, background render queue, reference matcher,
light follow rig and quick rename. All panels live in the 3D Viewport sidebar (N), tab "Studio".
"""
from . import delivery, render_queue, reference_match, light_rig, quick_rename

_modules = (delivery, render_queue, reference_match, light_rig, quick_rename)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
