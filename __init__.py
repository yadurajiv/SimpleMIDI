import bpy
import sys
import os

def import_dependencies():
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    wheels_path = os.path.join(addon_dir, "wheels")
    if wheels_path not in sys.path: sys.path.append(wheels_path)
    try:
        import mido, rtmidi
        return True
    except ImportError:
        return False

dependencies_loaded = import_dependencies()
from . import MidiControl

# --- Dynamic Device List ---
def get_midi_devices(self, context):
    items = []
    if not dependencies_loaded:
        return [('NONE', "Dependencies Missing", "Check console")]
    try:
        import mido
        devices = mido.get_input_names()
        for dev in devices: items.append((dev, dev, "MIDI Device"))
        if not items: items.append(('NONE', "No Devices Found", ""))
    except Exception as e:
        items.append(('ERROR', "Error Scanning", str(e)))
    return items

def update_device_selection(self, context):
    if self.midi_device_enum != 'NONE' and self.midi_device_enum != 'ERROR':
        self.midi_device_name = self.midi_device_enum

# --- Data Structures ---
class MidiMapping(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="New Mapping")
    data_path: bpy.props.StringProperty(name="Data Path", description="Right-click property -> Copy Full Data Path")
    midi_cc: bpy.props.IntProperty(name="ID", default=0, min=0, max=127)
    use_note: bpy.props.BoolProperty(name="Use Note/Key", default=False)
    
    # Internal property to store the "Goal" value from MIDI
    target_value: bpy.props.FloatProperty(default=0.0)

    # --- Interpolation Settings ---
    smooth_speed: bpy.props.FloatProperty(
        name="Speed", 
        default=0.1, min=0.01, max=1.0, 
        description="Animation Speed. 0.01 = Slow Fade, 1.0 = Instant"
    )
    
    easing_mode: bpy.props.EnumProperty(
        name="Curve",
        description="Shape of the transition",
        items=[
            ('LINEAR', "Linear", "Steady speed"),
            ('EASE_IN', "Ease In", "Starts slow"),
            ('EASE_OUT', "Ease Out", "Ends slow"),
            ('SMOOTHSTEP', "Smoothstep", "S-Curve"),
        ],
        default='LINEAR'
    )
    
    min_value: bpy.props.FloatProperty(name="Min", default=0.0)
    max_value: bpy.props.FloatProperty(name="Max", default=1.0)

# --- UI Panel ---
class OBJECT_PT_MidiController(bpy.types.Panel):
    bl_label = "Midi Controller"
    bl_idname = "OBJECT_PT_midi_controller"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Midi'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Connection
        box = layout.box()
        box.label(text="Connection")
        row = box.row(align=True)
        if scene.use_manual_device_name:
            row.prop(scene, "midi_device_name", text="")
            row.prop(scene, "use_manual_device_name", text="", icon='UNPINNED', toggle=True)
        else:
            row.prop(scene, "midi_device_enum", text="")
            row.prop(scene, "use_manual_device_name", text="", icon='GREASEPENCIL', toggle=True)
        box.operator("wm.midi_connect", text="Connect", icon='NODE_COMPOSITING')

        # Monitor
        box = layout.box()
        box.label(text="Monitor", icon='SOUND')
        if scene.monitor_id == -1:
            box.label(text="Waiting...", icon='TIME')
        else:
            row = box.row()
            row.label(text=f"{scene.monitor_type} #{scene.monitor_id}")
            row.label(text=f"Val: {int(scene.monitor_val)}")

        # Mappings
        layout.label(text="Mappings")
        for index, mapping in enumerate(scene.midi_mappings):
            box = layout.box()
            
            # Header
            row = box.row()
            row.prop(mapping, "name", text="")
            row.operator("wm.midi_remove_mapping", text="", icon='X').index = index
            
            # Path
            col = box.column()
            col.prop(mapping, "data_path", text="Path")
            
            # ID and Type
            row = box.row()
            row.prop(mapping, "midi_cc", text="ID")
            row.prop(mapping, "use_note", text="Key Mode")

            # Animation Controls (Highlighted)
            sub = box.column(align=True)
            row = sub.row(align=True)
            row.prop(mapping, "easing_mode", text="")
            row.prop(mapping, "smooth_speed", text="Speed")

            # Range
            row = box.row(align=True)
            row.prop(mapping, "min_value", text="Min")
            row.prop(mapping, "max_value", text="Max")

        layout.separator()
        layout.operator("wm.midi_add_mapping", text="Add Mapping", icon='ADD')

# --- Operators ---
class MIDI_OT_Connect(bpy.types.Operator):
    bl_idname = "wm.midi_connect"
    bl_label = "Connect MIDI"
    def execute(self, context):
        device = context.scene.midi_device_name
        if device == "" or device == "NONE":
            self.report({'ERROR'}, "Select a device")
            return {'CANCELLED'}
        if MidiControl.start_listening(device):
            self.report({'INFO'}, f"Connected to {device}")
        else:
            self.report({'ERROR'}, f"Failed to connect")
        return {'FINISHED'}

class MIDI_OT_AddMapping(bpy.types.Operator):
    bl_idname = "wm.midi_add_mapping"
    bl_label = "Add Mapping"
    def execute(self, context):
        context.scene.midi_mappings.add()
        return {'FINISHED'}

class MIDI_OT_RemoveMapping(bpy.types.Operator):
    bl_idname = "wm.midi_remove_mapping"
    bl_label = "Remove Mapping"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.midi_mappings.remove(self.index)
        return {'FINISHED'}

# --- Registration ---
classes = (MidiMapping, OBJECT_PT_MidiController, MIDI_OT_Connect, MIDI_OT_AddMapping, MIDI_OT_RemoveMapping)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.midi_mappings = bpy.props.CollectionProperty(type=MidiMapping)
    bpy.types.Scene.midi_device_name = bpy.props.StringProperty(name="Device", default="")
    bpy.types.Scene.midi_device_enum = bpy.props.EnumProperty(name="Device List", items=get_midi_devices, update=update_device_selection)
    bpy.types.Scene.use_manual_device_name = bpy.props.BoolProperty(default=False)
    
    # Monitor Props
    bpy.types.Scene.monitor_type = bpy.props.StringProperty(name="Type", default="None")
    bpy.types.Scene.monitor_id = bpy.props.IntProperty(name="ID", default=-1)
    bpy.types.Scene.monitor_val = bpy.props.FloatProperty(name="Val", default=0.0)

def unregister():
    MidiControl.stop_listening()
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.midi_mappings
    del bpy.types.Scene.midi_device_name
    del bpy.types.Scene.midi_device_enum
    del bpy.types.Scene.use_manual_device_name
    del bpy.types.Scene.monitor_type
    del bpy.types.Scene.monitor_id
    del bpy.types.Scene.monitor_val

if __name__ == "__main__":
    register()