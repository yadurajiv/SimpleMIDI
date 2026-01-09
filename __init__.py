import bpy
import sys
import os
import queue
import math
import json
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ImportHelper, ExportHelper

# --- 1. DEPENDENCY LOADER ---
def import_dependencies():
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    wheels_path = os.path.join(addon_dir, "wheels")
    if wheels_path not in sys.path: sys.path.append(wheels_path)
    try:
        import mido, rtmidi
        return True
    except ImportError: return False

dependencies_loaded = import_dependencies()

# --- 2. CORE LOGIC ---

execution_queue = queue.SimpleQueue()
animation_states = {}
midi_input = None
is_connected = False

# Safe Math Namespace for eval()
SAFE_MATH = {
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'pi': math.pi, 'sqrt': math.sqrt, 'pow': math.pow,
    'abs': abs, 'round': round, 'min': min, 'max': max,
    'time': 0.0, 'frame': 0.0
}

def robust_path_split(path):
    parts = []
    current = ""
    in_quote = False
    in_bracket = False
    quote_char = None
    for char in path:
        if char in ["'", '"']:
            if not in_quote: in_quote, quote_char = True, char
            elif char == quote_char: in_quote, quote_char = False, None
        elif char == '[':
            if not in_quote: in_bracket = True
        elif char == ']':
            if not in_quote: in_bracket = False 
        if char == '.' and not in_quote and not in_bracket:
            parts.append(current); current = ""
        else: current += char
    if current: parts.append(current)
    return parts

def resolve_path(path):
    try:
        if not path.startswith("bpy."): return None, None, None
        parts = robust_path_split(path)
        obj = bpy
        for part in parts[1:-1]:
            if '[' in part and ']' in part:
                base_name, key_section = part.split('[', 1)
                key = key_section.rstrip(']').strip("'\"")
                if key.isdigit(): key = int(key)
                obj = getattr(obj, base_name)[key]
            else: obj = getattr(obj, part)
        last_part = parts[-1]; index = -1
        if '[' in last_part and ']' in last_part:
            prop_name, idx_section = last_part.split('[', 1)
            index = int(idx_section.rstrip(']'))
        else: prop_name = last_part
        return obj, prop_name, index
    except: return None, None, None

def apply_easing(t, mode):
    if t < 0: t = 0
    if t > 1: t = 1
    if mode == 'LINEAR': return t
    elif mode == 'QUAD_IN': return t * t
    elif mode == 'QUAD_OUT': return 1 - (1 - t) * (1 - t)
    elif mode == 'QUAD_INOUT': return 2 * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 2) / 2
    elif mode == 'CUBIC_OUT': return 1 - math.pow(1 - t, 3)
    elif mode == 'EXPO_OUT': return 1 if t == 1 else 1 - math.pow(2, -10 * t)
    elif mode == 'BACK_OUT': c1 = 1.70158; c3 = c1 + 1; return 1 + c3 * math.pow(t - 1, 3) + c1 * math.pow(t - 1, 2)
    elif mode == 'ELASTIC_OUT':
        if t == 0: return 0
        if t == 1: return 1
        c4 = (2 * math.pi) / 3
        return math.pow(2, -10 * t) * math.sin((t * 10 - 0.75) * c4) + 1
    elif mode == 'BOUNCE_OUT':
        n1 = 7.5625; d1 = 2.75
        if t < 1 / d1: return n1 * t * t
        elif t < 2 / d1: t -= 1.5 / d1; return n1 * t * t + 0.75
        elif t < 2.5 / d1: t -= 2.25 / d1; return n1 * t * t + 0.9375
        else: t -= 2.625 / d1; return n1 * t * t + 0.984375
    return t

def apply_to_blender(target, input_val, use_absolute_midi):
    """
    input_val: The 0.0-1.0 interpolated value from the knob/key.
    """
    obj, prop_name, index = resolve_path(target.data_path)
    if obj is None: return

    # --- MATH & LOGIC ---
    
    # 1. Prepare Variables for Math
    # x = standard 0-1 value
    # abs_x = raw midi 0-127 value
    
    # Update time variables
    SAFE_MATH['frame'] = bpy.context.scene.frame_current
    SAFE_MATH['time'] = bpy.context.scene.frame_current / (bpy.context.scene.render.fps or 24)
    SAFE_MATH['x'] = input_val
    
    final_val = 0.0

    # 2. Check for Custom Expression
    if target.expression.strip() != "":
        try:
            # Evaluate string: e.g. "sin(time * x * 5)"
            final_val = float(eval(target.expression, {"__builtins__": None}, SAFE_MATH))
        except:
            # Fallback if math fails
            final_val = input_val
    else:
        # 3. Standard Logic (Min/Max)
        if use_absolute_midi:
            final_val = input_val * 127.0
        else:
            final_val = target.min_value + (input_val * (target.max_value - target.min_value))

    # 4. Handle Motor Mode (Accumulate) vs Standard (Set)
    try:
        # Get current value to add to it, or overwrite it
        if index != -1:
            current_vector = getattr(obj, prop_name)
            current_val = current_vector[index]
        else:
            current_val = getattr(obj, prop_name)

        if target.drive_mode == 'ACCUMULATE':
            # Motor Mode: Value += Input * Scale
            # We treat 'final_val' as the Speed/Delta
            # If no expression, we center it: 0.5 is stop, 1.0 is fwd, 0.0 is back
            
            delta = 0.0
            if target.expression.strip() != "":
                delta = final_val # Use expression result directly as speed
            else:
                # Default Motor Logic: 
                # Knob middle (0.5) = Stop.
                # Knob right (>0.5) = Forward.
                # Knob left (<0.5) = Backward.
                delta = (final_val - (target.max_value + target.min_value)/2) * 0.1 # Arbitrary speed factor
            
            new_val = current_val + delta
            
        else:
            # Standard Mode: Direct Set
            new_val = final_val

        # 5. Apply & Keyframe
        target.current_val_display = new_val

        if index != -1:
            current_vector[index] = new_val
        else:
            setattr(obj, prop_name, new_val)
            
        if bpy.context.scene.tool_settings.use_keyframe_insert_auto:
            obj.keyframe_insert(data_path=prop_name, index=index if index != -1 else -1)

    except Exception: pass

def midi_callback(message):
    if message.type == 'control_change':
        execution_queue.put(('CC', message.control, message.value))
    elif message.type == 'note_on':
        execution_queue.put(('Note', message.note, message.velocity))
    elif message.type == 'note_off':
        execution_queue.put(('Note', message.note, 0))

def queue_processor():
    scene = bpy.context.scene
    
    while not execution_queue.empty():
        m_type, m_id, m_val = execution_queue.get()
        try:
            scene.monitor_type = m_type
            scene.monitor_id = m_id
            scene.monitor_val = float(m_val)
        except: pass

        for mapping in scene.midi_mappings:
            if mapping.midi_cc == m_id:
                if mapping.use_note:
                    if m_type == 'Note': mapping.target_value = 1.0 if m_val > 0 else 0.0
                else:
                    if m_type != 'Note': mapping.target_value = m_val / 127.0

    for i, mapping in enumerate(scene.midi_mappings):
        if i not in animation_states: animation_states[i] = mapping.target_value
        
        prev_val = animation_states[i]
        target = mapping.target_value
        current = prev_val
        
        if abs(current - target) < 0.001: current = target
        else:
            speed = mapping.smooth_speed
            step = speed * 0.2 if speed < 1.0 else 1.0
            if current < target:
                current += step
                if current > target: current = target
            else:
                current -= step
                if current < target: current = target
        
        # Optimize: Only update if changed OR if using Motor/Math mode (which might depend on time)
        # We need to check if any target uses Time/Frame variables or Accumulate mode
        is_active_mode = any(t.drive_mode == 'ACCUMULATE' or 'time' in t.expression or 'frame' in t.expression for t in mapping.targets)
        
        if abs(current - prev_val) > 0.00001 or (is_active_mode and current > 0.01):
            animation_states[i] = current
            curved_val = apply_easing(current, mapping.easing_mode)
            for target in mapping.targets:
                apply_to_blender(target, curved_val, mapping.use_absolute)
        
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()
    return 0.016 

def start_listening(port_name):
    import mido
    global midi_input, is_connected
    try:
        stop_listening()
        midi_input = mido.open_input(port_name, callback=midi_callback)
        is_connected = True
        if not bpy.app.timers.is_registered(queue_processor):
            bpy.app.timers.register(queue_processor)
        return True
    except: is_connected = False; return False

def stop_listening():
    global midi_input, is_connected
    if 'midi_input' in globals() and midi_input: midi_input.close(); midi_input = None
    is_connected = False
    if bpy.app.timers.is_registered(queue_processor): bpy.app.timers.unregister(queue_processor)

# --- 3. UI & PROPS ---

@persistent
def auto_select_device(dummy):
    if not dependencies_loaded: return
    try:
        import mido
        devices = mido.get_input_names()
        if devices and bpy.context.scene.midi_device_name == "":
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

class MidiTarget(bpy.types.PropertyGroup):
    data_path: bpy.props.StringProperty(name="Path")
    min_value: bpy.props.FloatProperty(name="Min", default=0.0)
    max_value: bpy.props.FloatProperty(name="Max", default=1.0)
    current_val_display: bpy.props.FloatProperty(default=0.0, options={'SKIP_SAVE'})
    
    # NEW FEATURES
    drive_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[('SET', "Set (Standard)", ""), ('ACCUMULATE', "Motor (Accumulate)", "")],
        default='SET'
    )
    expression: bpy.props.StringProperty(name="Math", description="e.g: sin(time * x * 10). Vars: x (input 0-1), time, frame")

class MidiMapping(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="Mapping")
    midi_cc: bpy.props.IntProperty(name="ID", default=0, min=0, max=127)
    use_note: bpy.props.BoolProperty(name="Key Mode", default=False)
    target_value: bpy.props.FloatProperty(default=0.0)
    targets: bpy.props.CollectionProperty(type=MidiTarget)
    show_expanded: bpy.props.BoolProperty(name="Show Details", default=True)
    use_absolute: bpy.props.BoolProperty(name="Abs", default=False)
    smooth_speed: bpy.props.FloatProperty(name="Speed", default=0.1, min=0.01, max=1.0)
    easing_mode: bpy.props.EnumProperty(
        name="Curve",
        items=[('LINEAR', "Linear", ""), ('QUAD_IN', "Quad In", ""), ('QUAD_OUT', "Quad Out", ""),
               ('QUAD_INOUT', "Quad Smooth", ""), ('CUBIC_OUT', "Cubic", ""), ('EXPO_OUT', "Expo", ""),
               ('BACK_OUT', "Back", ""), ('ELASTIC_OUT', "Elastic", ""), ('BOUNCE_OUT', "Bounce", "")],
        default='LINEAR'
    )

class OBJECT_PT_MidiController(bpy.types.Panel):
    bl_label = "Midi Controller"
    bl_idname = "OBJECT_PT_midi_controller"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Midi'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        global is_connected
        
        box = layout.box()
        row = box.row(align=True)
        if not is_connected: row.prop(scene, "midi_device_enum", text="")
        else: row.label(text=f"Connected: {scene.midi_device_name}", icon='CHECKBOX_HLT')
        
        if not is_connected: row.operator("wm.midi_connect", text="Connect", icon='PLAY') 
        else: 
            btn = row.operator("wm.midi_disconnect", text="Disconnect", icon='PAUSE')
            row.alert = True

        box = layout.box()
        if not is_connected: box.label(text="Status: Disconnected", icon='CANCEL')
        elif scene.monitor_id == -1: box.label(text="Monitor: Waiting...", icon='SOUND')
        else: box.label(text=f"Monitor: {scene.monitor_type} #{scene.monitor_id} (Val: {int(scene.monitor_val)})", icon='SOUND')

        row = box.row(align=True)
        row.operator("wm.midi_export_json", text="Export", icon='EXPORT')
        row.operator("wm.midi_import_json", text="Import", icon='IMPORT')

        layout.label(text="Mappings")
        for index, mapping in enumerate(scene.midi_mappings):
            box = layout.box()
            row = box.row(align=True)
            icon = 'TRIA_DOWN' if mapping.show_expanded else 'TRIA_RIGHT'
            row.prop(mapping, "show_expanded", text="", icon=icon, emboss=False)
            row.prop(mapping, "name", text="")
            row.operator("wm.midi_duplicate_mapping", text="", icon='DUPLICATE').index = index
            row.operator("wm.midi_remove_mapping", text="", icon='X').index = index
            
            if mapping.show_expanded:
                col = box.column()
                row = col.row(align=True)
                row.prop(mapping, "midi_cc", text="ID")
                row.prop(mapping, "use_note", text="Key")
                row.prop(mapping, "use_absolute", text="Abs")
                row = col.row(align=True)
                row.prop(mapping, "easing_mode", text="")
                row.prop(mapping, "smooth_speed", text="Speed")

                t_col = col.column(align=True)
                t_col.label(text="Targets:")
                for t_index, target in enumerate(mapping.targets):
                    t_box = t_col.box()
                    t_row = t_box.row()
                    t_row.prop(target, "data_path", text="")
                    t_row.operator("wm.midi_remove_target", text="", icon='REMOVE').mapping_index = index
                    
                    # Logic Row
                    t_row = t_box.row(align=True)
                    t_row.prop(target, "drive_mode", text="") # Set vs Motor
                    
                    if target.expression:
                         t_row.prop(target, "expression", text="Expr", icon='SCRIPT')
                    else:
                        if not mapping.use_absolute:
                            t_row.prop(target, "min_value", text="Min")
                            t_row.prop(target, "max_value", text="Max")
                        else:
                            t_row.label(text="(Abs Mode)")
                    
                    # Expression Field (Full width if typed in)
                    if target.expression:
                         t_box.prop(target, "expression", text="Math")
                    else:
                         # Tiny button to reveal math
                         t_box.prop(target, "expression", text="Add Math...")

                col.operator("wm.midi_add_target", text="Add Target", icon='ADD').mapping_index = index

        layout.operator("wm.midi_add_mapping", text="New Mapping", icon='ADD')

# --- OPERATORS (Unchanged except JSON Update) ---
# ... (Standard Connect/Disconnect/Add/Remove/Duplicate ops same as before) ...
# Need to update Export/Import for new properties 'drive_mode' and 'expression'

class MIDI_OT_Connect(bpy.types.Operator):
    bl_idname = "wm.midi_connect"
    bl_label = "Connect"
    def execute(self, context):
        if start_listening(context.scene.midi_device_name): self.report({'INFO'}, "Connected")
        else: self.report({'ERROR'}, "Failed")
        return {'FINISHED'}

class MIDI_OT_Disconnect(bpy.types.Operator):
    bl_idname = "wm.midi_disconnect"
    bl_label = "Disconnect"
    def execute(self, context):
        stop_listening()
        self.report({'INFO'}, "Disconnected")
        return {'FINISHED'}

class MIDI_OT_AddMapping(bpy.types.Operator):
    bl_idname = "wm.midi_add_mapping"
    bl_label = "Add Mapping"
    def execute(self, context):
        new_map = context.scene.midi_mappings.add()
        if context.scene.monitor_id != -1:
            new_map.midi_cc = context.scene.monitor_id
            new_map.use_note = (context.scene.monitor_type == 'Note')
            new_map.name = f"{context.scene.monitor_type} {context.scene.monitor_id}"
        new_map.targets.add()
        return {'FINISHED'}

class MIDI_OT_DuplicateMapping(bpy.types.Operator):
    bl_idname = "wm.midi_duplicate_mapping"
    bl_label = "Duplicate"
    index: bpy.props.IntProperty()
    def execute(self, context):
        src = context.scene.midi_mappings[self.index]
        new_map = context.scene.midi_mappings.add()
        new_map.name = src.name + " Copy"
        new_map.midi_cc = src.midi_cc
        new_map.use_note = src.use_note
        new_map.use_absolute = src.use_absolute
        new_map.smooth_speed = src.smooth_speed
        new_map.easing_mode = src.easing_mode
        for t in src.targets:
            new_t = new_map.targets.add()
            new_t.data_path = t.data_path
            new_t.min_value = t.min_value
            new_t.max_value = t.max_value
            new_t.drive_mode = t.drive_mode
            new_t.expression = t.expression
        return {'FINISHED'}

class MIDI_OT_RemoveMapping(bpy.types.Operator):
    bl_idname = "wm.midi_remove_mapping"
    bl_label = "Remove"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.midi_mappings.remove(self.index)
        return {'FINISHED'}

class MIDI_OT_AddTarget(bpy.types.Operator):
    bl_idname = "wm.midi_add_target"
    bl_label = "Add Target"
    mapping_index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.midi_mappings[self.mapping_index].targets.add()
        return {'FINISHED'}

class MIDI_OT_RemoveTarget(bpy.types.Operator):
    bl_idname = "wm.midi_remove_target"
    bl_label = "Remove Target"
    mapping_index: bpy.props.IntProperty()
    def execute(self, context):
        targets = context.scene.midi_mappings[self.mapping_index].targets
        if len(targets) > 0: targets.remove(len(targets)-1) 
        return {'FINISHED'}

class MIDI_OT_ExportJSON(bpy.types.Operator, ExportHelper):
    bl_idname = "wm.midi_export_json"
    bl_label = "Export"
    filename_ext = ".json"
    def execute(self, context):
        data = []
        for m in context.scene.midi_mappings:
            targets_data = []
            for t in m.targets:
                targets_data.append({
                    "path": t.data_path, "min": t.min_value, "max": t.max_value,
                    "mode": t.drive_mode, "expr": t.expression
                })
            data.append({
                "name": m.name, "cc": m.midi_cc, "note": m.use_note,
                "abs": m.use_absolute, "speed": m.smooth_speed, "curve": m.easing_mode,
                "targets": targets_data
            })
        with open(self.filepath, 'w') as f: json.dump(data, f, indent=4)
        return {'FINISHED'}

class MIDI_OT_ImportJSON(bpy.types.Operator, ImportHelper):
    bl_idname = "wm.midi_import_json"
    bl_label = "Import"
    filename_ext = ".json"
    use_selected: bpy.props.BoolProperty(name="Use Selected Object", default=True)
    def execute(self, context):
        try:
            with open(self.filepath, 'r') as f: data = json.load(f)
        except: return {'CANCELLED'}
        active_obj = context.active_object
        for entry in data:
            nm = context.scene.midi_mappings.add()
            nm.name = entry.get("name", "Import")
            nm.midi_cc = entry.get("cc", 0)
            nm.use_note = entry.get("note", False)
            nm.use_absolute = entry.get("abs", False)
            nm.smooth_speed = entry.get("speed", 0.1)
            nm.easing_mode = entry.get("curve", 'LINEAR')
            for t_data in entry.get("targets", []):
                nt = nm.targets.add()
                nt.min_value = t_data.get("min", 0.0)
                nt.max_value = t_data.get("max", 1.0)
                nt.drive_mode = t_data.get("mode", 'SET')
                nt.expression = t_data.get("expr", "")
                raw_path = t_data.get("path", "")
                if self.use_selected and active_obj and "bpy.data.objects" in raw_path:
                    try:
                        str_parts = raw_path.split('].', 1)
                        if len(str_parts) > 1: nt.data_path = f'bpy.data.objects["{active_obj.name}"].{str_parts[1]}'
                        else: nt.data_path = raw_path
                    except: nt.data_path = raw_path
                else: nt.data_path = raw_path
        return {'FINISHED'}

classes = (MidiTarget, MidiMapping, OBJECT_PT_MidiController, MIDI_OT_Connect, MIDI_OT_Disconnect, MIDI_OT_AddMapping, MIDI_OT_DuplicateMapping, MIDI_OT_RemoveMapping, MIDI_OT_AddTarget, MIDI_OT_RemoveTarget, MIDI_OT_ExportJSON, MIDI_OT_ImportJSON)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.midi_mappings = bpy.props.CollectionProperty(type=MidiMapping)
    bpy.types.Scene.midi_device_name = bpy.props.StringProperty()
    bpy.types.Scene.midi_device_enum = bpy.props.EnumProperty(items=get_midi_devices, update=update_device_selection)
    bpy.types.Scene.use_manual_device_name = bpy.props.BoolProperty()
    bpy.types.Scene.monitor_type = bpy.props.StringProperty(default="None")
    bpy.types.Scene.monitor_id = bpy.props.IntProperty(default=-1)
    bpy.types.Scene.monitor_val = bpy.props.FloatProperty()
    if auto_select_device not in bpy.app.handlers.load_post: bpy.app.handlers.load_post.append(auto_select_device)
    bpy.app.timers.register(lambda: (auto_select_device(None), None)[1], first_interval=1.0)

def unregister():
    stop_listening()
    if auto_select_device in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(auto_select_device)
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.midi_mappings

if __name__ == "__main__":
    register()