import bpy
import sys
import os
from bpy.app.handlers import persistent

def import_dependencies():
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    wheels_path = os.path.join(addon_dir, "wheels")
    if wheels_path not in sys.path: sys.path.append(wheels_path)
    try:
        import mido, rtmidi
        return True
    except ImportError: return False

dependencies_loaded = import_dependencies()
from . import MidiControl

# --- Auto-Select Handler ---
@persistent
def auto_select_device(dummy):
    """ Runs on startup to auto-select the first MIDI device """
    if not dependencies_loaded: return
    try:
        import mido
        devices = mido.get_input_names()
        if devices and bpy.context.scene.midi_device_name == "":
            print(f"MidiController: Auto-selecting {devices[0]}")
            bpy.context.scene.midi_device_name = devices[0]
            bpy.context.scene.midi_device_enum = devices[0]
    except: pass

def get_midi_devices(self, context):
    items = []
    if not dependencies_loaded: return [('NONE', "Missing Dependencies", "")]
    try:
        import mido
        devices = mido.get_input_names()
        for dev in devices: items.append((dev, dev, "MIDI Device"))
        if not items: items.append(('NONE', "No Devices Found", ""))
    except Exception as e: items.append(('ERROR', str(e), ""))
    return items

def update_device_selection(self, context):
    if self.midi_device_enum not in ['NONE', 'ERROR']:
        self.midi_device_name = self.midi_device_enum

class MidiMapping(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="New Mapping")
    data_path: bpy.props.StringProperty(name="Data Path", description="Right-click property -> Copy Full Data Path")
    midi_cc: bpy.props.IntProperty(name="ID", default=0, min=0, max=127)
    use_note: bpy.props.BoolProperty(name="Use Note/Key", default=False)
    
    # Logic
    target_value: bpy.props.FloatProperty(default=0.0)
    current_output_value: bpy.props.FloatProperty(default=0.0) # For UI Display
    
    # Settings
    use_absolute: bpy.props.BoolProperty(name="Abs", description="Use raw MIDI value (0-127) instead of Min/Max range", default=False)
    smooth_speed: bpy.props.FloatProperty(name="Speed", default=0.1, min=0.01, max=1.0)
    
    easing_mode: bpy.props.EnumProperty(
        name="Curve",
        items=[
            ('LINEAR', "Linear", "Steady speed"),
            ('QUAD_IN', "Quad In", "Slow start"),
            ('QUAD_OUT', "Quad Out", "Slow end"),
            ('QUAD_INOUT', "Quad In/Out", "Slow start and end"),
            ('CUBIC_OUT', "Cubic Out", "Slower end"),
            ('EXPO_OUT', "Expo Out", "Very sharp snap"),
            ('BACK_OUT', "Back Out", "Overshoot"),
            ('ELASTIC_OUT', "Elastic Out", "Wobbly"),
            ('BOUNCE_OUT', "Bounce Out", "Bounce"),
        ],
        default='LINEAR'
    )
    min_value: bpy.props.FloatProperty(name="Min", default=0.0)
    max_value: bpy.props.FloatProperty(name="Max", default=1.0)

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
            
            # Header with Name + Value Display
            row = box.row()
            row.prop(mapping, "name", text="")
            # Show live value
            row.label(text=f"Val: {mapping.current_output_value:.2f}")
            row.operator("wm.midi_remove_mapping", text="", icon='X').index = index
            
            col = box.column()
            col.prop(mapping, "data_path", text="Path")
            
            # ID | Key Mode | Absolute Toggle
            row = box.row(align=True)
            row.prop(mapping, "midi_cc", text="ID")
            row.prop(mapping, "use_note", text="Key")
            row.prop(mapping, "use_absolute", text="Abs")

            # Animation
            sub = box.column(align=True)
            row = sub.row(align=True)
            row.prop(mapping, "easing_mode", text="")
            row.prop(mapping, "smooth_speed", text="Speed")

            # Range (Only show if NOT Absolute)
            if not mapping.use_absolute:
                row = box.row(align=True)
                row.prop(mapping, "min_value", text="Min")
                row.prop(mapping, "max_value", text="Max")
            else:
                row = box.row()
                row.label(text="Output: 0 - 127 (Absolute)", icon='INFO')

        layout.separator()
        layout.operator("wm.midi_add_mapping", text="Add Mapping", icon='ADD')

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
        new_map = context.scene.midi_mappings.add()
        if context.scene.monitor_id != -1:
            new_map.midi_cc = context.scene.monitor_id
            if context.scene.monitor_type == 'Note':
                new_map.use_note = True
                new_map.name = f"Key {context.scene.monitor_id}"
            else:
                new_map.use_note = False
                new_map.name = f"Knob {context.scene.monitor_id}"
        return {'FINISHED'}

class MIDI_OT_RemoveMapping(bpy.types.Operator):
    bl_idname = "wm.midi_remove_mapping"
    bl_label = "Remove Mapping"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.midi_mappings.remove(self.index)
        return {'FINISHED'}

classes = (MidiMapping, OBJECT_PT_MidiController, MIDI_OT_Connect, MIDI_OT_AddMapping, MIDI_OT_RemoveMapping)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.midi_mappings = bpy.props.CollectionProperty(type=MidiMapping)
    bpy.types.Scene.midi_device_name = bpy.props.StringProperty(name="Device", default="")
    bpy.types.Scene.midi_device_enum = bpy.props.EnumProperty(name="Device List", items=get_midi_devices, update=update_device_selection)
    bpy.types.Scene.use_manual_device_name = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.monitor_type = bpy.props.StringProperty(name="Type", default="None")
    bpy.types.Scene.monitor_id = bpy.props.IntProperty(name="ID", default=-1)
    bpy.types.Scene.monitor_val = bpy.props.FloatProperty(name="Val", default=0.0)
    
    if auto_select_device not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(auto_select_device)
    bpy.app.timers.register(lambda: (auto_select_device(None), None)[1], first_interval=1.0)

def unregister():
    MidiControl.stop_listening()
    if auto_select_device in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_select_device)
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.midi_mappings
    # cleanup

if __name__ == "__main__":
    register()