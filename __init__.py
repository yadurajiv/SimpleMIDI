import bpy
import sys
import os

# --- Dependency Loader ---
def import_dependencies():
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    wheels_path = os.path.join(addon_dir, "wheels")
    if wheels_path not in sys.path: sys.path.append(wheels_path)
    try:
        import mido, rtmidi
        return True
    except ImportError:
        return False

# Load immediately
dependencies_loaded = import_dependencies()
from . import MidiControl

# --- Dynamic Device List ---
def get_midi_devices(self, context):
    """
    Callback to populate the EnumProperty with detected MIDI ports.
    """
    items = []
    
    if not dependencies_loaded:
        return [('NONE', "Dependencies Missing", "Check console for errors")]

    try:
        import mido
        # Get list of input names
        devices = mido.get_input_names()
        
        # Format: (identifier, name, description)
        for dev in devices:
            # We use the device name as the identifier
            items.append((dev, dev, "MIDI Device"))
            
        if not items:
            items.append(('NONE', "No Devices Found", "Plug in a device and try again"))
            
    except Exception as e:
        items.append(('ERROR', "Error Scanning", str(e)))
        
    return items

def update_device_selection(self, context):
    """
    When the Dropdown changes, copy the value to the 'Real' device name string.
    """
    if self.midi_device_enum != 'NONE' and self.midi_device_enum != 'ERROR':
        self.midi_device_name = self.midi_device_enum

# --- Data Structures ---
class MidiMapping(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="New Mapping")
    data_path: bpy.props.StringProperty(name="Data Path", description="Right-click property -> Copy Full Data Path")
    midi_cc: bpy.props.IntProperty(name="ID", default=0, min=0, max=127, description="CC Number or Note Number")
    use_note: bpy.props.BoolProperty(name="Use Note/Key", default=False, description="Enable if using a Keyboard Key (Note) instead of a Knob (CC)")
    min_value: bpy.props.FloatProperty(name="Min / Release", default=0.0)
    max_value: bpy.props.FloatProperty(name="Max / Press", default=1.0)

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
        
        # --- Connection ---
        box = layout.box()
        box.label(text="Connection")
        
        # Row for Device Selection
        row = box.row(align=True)
        
        if scene.use_manual_device_name:
            # TEXT MODE
            row.prop(scene, "midi_device_name", text="")
            # Button to switch back to Dropdown
            row.prop(scene, "use_manual_device_name", text="", icon='Unpinned', toggle=True)
        else:
            # DROPDOWN MODE
            row.prop(scene, "midi_device_enum", text="")
            # Button to switch to Manual Text
            row.prop(scene, "use_manual_device_name", text="", icon='GREASEPENCIL', toggle=True)

        # Connect Button
        box.operator("wm.midi_connect", text="Connect", icon='NODE_COMPOSITING')

        # --- MIDI MONITOR ---
        box = layout.box()
        box.label(text="Monitor (Last Signal)", icon='SOUND')
        
        if scene.monitor_id == -1:
            box.label(text="Waiting for signal...", icon='TIME')
        else:
            signal_name = f"{scene.monitor_type} #{scene.monitor_id}"
            row = box.row()
            row.label(text=signal_name)
            row.label(text=f"Val: {int(scene.monitor_val)}")
            if scene.monitor_type == "Note":
                box.label(text="Tip: Check 'Use Note/Key'!", icon='INFO')

        # --- Mappings ---
        layout.label(text="Mappings")
        for index, mapping in enumerate(scene.midi_mappings):
            box = layout.box()
            row = box.row()
            row.prop(mapping, "name", text="")
            row.operator("wm.midi_remove_mapping", text="", icon='X').index = index
            
            col = box.column()
            col.prop(mapping, "data_path", text="Path")
            
            row = box.row()
            row.prop(mapping, "midi_cc", text="ID (CC/Note)")
            row.prop(mapping, "use_note", text="Key Mode")
            
            row = box.row(align=True)
            row.prop(mapping, "min_value", text="Min/Off")
            row.prop(mapping, "max_value", text="Max/On")

        layout.separator()
        layout.operator("wm.midi_add_mapping", text="Add Mapping", icon='ADD')

# --- Operators ---
class MIDI_OT_Connect(bpy.types.Operator):
    bl_idname = "wm.midi_connect"
    bl_label = "Connect MIDI"
    def execute(self, context):
        device = context.scene.midi_device_name
        
        if device == "" or device == "NONE":
            self.report({'ERROR'}, "Please select or type a valid device name")
            return {'CANCELLED'}

        if MidiControl.start_listening(device):
            self.report({'INFO'}, f"Connected to {device}")
        else:
            self.report({'ERROR'}, f"Failed to connect. Check name.")
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
    
    # The 'Real' Name used by the backend
    bpy.types.Scene.midi_device_name = bpy.props.StringProperty(name="Device", default="")
    
    # The Dropdown (Enum) used by the UI
    bpy.types.Scene.midi_device_enum = bpy.props.EnumProperty(
        name="Device List",
        description="Available MIDI Devices",
        items=get_midi_devices,
        update=update_device_selection
    )
    
    # Toggle between Text Box and Dropdown
    bpy.types.Scene.use_manual_device_name = bpy.props.BoolProperty(
        name="Manual Entry",
        description="Switch between Dropdown list and Manual Text Entry",
        default=False
    )
    
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