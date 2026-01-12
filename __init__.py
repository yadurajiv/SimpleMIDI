
import bpy
import sys
import os
import math
import json
import ast
import operator
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ImportHelper, ExportHelper

# DEPENDENCIES
import mido
import rtmidi

# ==============================================================================
# SECTION 1: SAFE MATH EVALUATOR
# ==============================================================================
# Whitelist of allowed math functions
SAFE_FUNCTIONS = {
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'pi': math.pi, 'sqrt': math.sqrt, 'pow': math.pow,
    'abs': abs, 'round': round, 'min': min, 'max': max,
}

# Whitelist of allowed operators (+, -, *, /)
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def _eval_node(node, context):
    """Recursively evaluates an AST node with strict safety checks."""
    if isinstance(node, ast.Constant):  # Numbers (Python 3.8+)
        return node.value
    elif isinstance(node, ast.Num):     # Numbers (Legacy)
        return node.n
    elif isinstance(node, ast.Name):    # Variables (x, time, frame)
        return context.get(node.id, 0.0)
    elif isinstance(node, ast.BinOp):   # Math operations (left + right)
        op_func = SAFE_OPERATORS.get(type(node.op))
        if op_func:
            return op_func(_eval_node(node.left, context), _eval_node(node.right, context))
    elif isinstance(node, ast.UnaryOp): # Unary operations (-1)
        op_func = SAFE_OPERATORS.get(type(node.op))
        if op_func:
            return op_func(_eval_node(node.operand, context))
    elif isinstance(node, ast.Call):    # Functions (sin, cos)
        func_name = node.func.id
        if func_name in SAFE_FUNCTIONS:
            args = [_eval_node(arg, context) for arg in node.args]
            return SAFE_FUNCTIONS[func_name](*args)
    return 0.0

def safe_evaluate_expression(expression, context):
    """Parses and evaluates a math string without using eval()."""
    try:
        if not expression or not expression.strip():
            return 0.0
        # Parse expression into an Abstract Syntax Tree (AST)
        tree = ast.parse(expression, mode='eval')
        # Walk the tree and calculate
        return float(_eval_node(tree.body, context))
    except Exception:
        return 0.0

# ==============================================================================
# SECTION 2: CORE LOGIC & STATE
# ==============================================================================

animation_states = {} # Stores current smoothed values for interpolation
midi_input = None     # The active MIDI port object
is_connected = False  # Connection flag

def robust_path_split(path):
    """
    Splits a data path by dots (.), ignoring dots inside quotes ["..."].
    """
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
        
        # Split ONLY if it's a dot and we are NOT inside quotes or brackets
        if char == '.' and not in_quote and not in_bracket:
            parts.append(current); current = ""
        else: current += char
        
    if current: parts.append(current)
    return parts

def resolve_path(path):
    """
    Walks down the Blender data path to find the object and property.
    Returns: (parent_object, property_name, index)
    """
    try:
        if not path.startswith("bpy."): return None, None, -1
        
        parts = robust_path_split(path)
        obj = bpy
        
        # Navigate to the parent object (2nd to last item)
        parent_parts = parts[1:-1]
        target_prop = parts[-1] 
        
        for part in parent_parts:
            # Handle dictionary/array access like: nodes["Group"]
            if '[' in part and part.endswith(']'):
                base_name, key_section = part.split('[', 1)
                key_raw = key_section.rstrip(']')
                
                obj = getattr(obj, base_name)
                
                # Parse Key (String or Int)
                if (key_raw.startswith('"') and key_raw.endswith('"')) or \
                   (key_raw.startswith("'") and key_raw.endswith("'")):
                    key = key_raw[1:-1] # String key
                elif key_raw.isdigit():
                    key = int(key_raw)  # Int key
                else:
                    key = key_raw       # Fallback
                
                obj = obj[key]
            else:
                obj = getattr(obj, part)
        
        # Parse the final property to see if it has an index (e.g., location[0])
        index = -1
        prop_name = target_prop
        
        if '[' in target_prop and target_prop.endswith(']'):
            base_name, idx_str = target_prop.split('[', 1)
            prop_name = base_name
            idx_clean = idx_str.rstrip(']')
            if idx_clean.isdigit():
                index = int(idx_clean)
        
        return obj, prop_name, index
        
    except Exception:
        return None, None, None

def apply_easing(t, mode):
    """Applies physics curves to the 0-1 interpolation value."""
    if t < 0: t = 0
    if t > 1: t = 1
    if mode == 'LINEAR': return t
    elif mode == 'QUAD_IN': return t * t
    elif mode == 'QUAD_OUT': return 1 - (1 - t) * (1 - t)
    elif mode == 'QUAD_INOUT': return 2 * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 2) / 2
    elif mode == 'CUBIC_OUT': return 1 - math.pow(1 - t, 3)
    elif mode == 'EXPO_OUT': return 1 if t == 1 else 1 - math.pow(2, -10 * t)
    elif mode == 'BACK_OUT': 
        c1 = 1.70158; c3 = c1 + 1
        return 1 + c3 * math.pow(t - 1, 3) + c1 * math.pow(t - 1, 2)
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
    obj, prop_name, index = resolve_path(target.data_path)
    if obj is None: return

    # Prepare Context
    math_ctx = SAFE_FUNCTIONS.copy()
    math_ctx['frame'] = bpy.context.scene.frame_current
    math_ctx['time'] = bpy.context.scene.frame_current / (bpy.context.scene.render.fps or 24)
    math_ctx['x'] = input_val
    
    # 1. Calculate Target Value
    final_val = 0.0
    if target.expression.strip() != "":
        final_val = safe_evaluate_expression(target.expression, math_ctx)
    else:
        if use_absolute_midi: final_val = input_val * 127.0
        else: final_val = target.min_value + (input_val * (target.max_value - target.min_value))

    try:
        # 2. Get Current Value & Type
        if index != -1:
            container = getattr(obj, prop_name)
            current_val = container[index]
        else:
            current_val = getattr(obj, prop_name)

        # 3. Apply Drive Mode Logic
        if target.drive_mode == 'ACCUMULATE':
            new_val = current_val + final_val
        else:
            new_val = final_val

        # --- BETTER TYPE HANDLING ---
        # Detect the type of the property we are controlling and adapt.
        
        if isinstance(current_val, bool):
            # BOOLEAN: Threshold at 0.5 (Snap On/Off)
            new_val = (new_val >= 0.5)
            
        elif isinstance(current_val, int):
            # INTEGER: Round to nearest whole number
            new_val = int(round(new_val))
            
        # FLOATS are passed through directly (no change needed)

        target.current_val_display = float(new_val)

        # 4. Write & Keyframe
        if index != -1:
            container[index] = new_val
            if bpy.context.scene.tool_settings.use_keyframe_insert_auto:
                obj.keyframe_insert(data_path=prop_name, index=index)
        else:
            setattr(obj, prop_name, new_val)
            if bpy.context.scene.tool_settings.use_keyframe_insert_auto:
                obj.keyframe_insert(data_path=prop_name)

    except Exception: pass

# ==============================================================================
# SECTION 3: MIDI POLLING
# ==============================================================================

def process_midi_events():
    """
    Run on the Main Thread via a timer. 
    Polls the MIDI buffer for new messages and updates the scene.
    """
    if not is_connected or midi_input is None:
        return 0.1 # Poll slowly if disconnected

    scene = bpy.context.scene
    
    # 1. READ PENDING MESSAGES
    for message in midi_input.iter_pending():
        m_type = 'None'
        m_id = -1
        m_val = 0
        
        if message.type == 'control_change':
            m_type, m_id, m_val = 'CC', message.control, message.value
        elif message.type == 'note_on':
            m_type, m_id, m_val = 'Note', message.note, message.velocity
        elif message.type == 'note_off':
            m_type, m_id, m_val = 'Note', message.note, 0
            
        # Update UI Monitor (Used for Auto-Assign)
        scene.monitor_type = m_type
        scene.monitor_id = m_id
        scene.monitor_val = float(m_val)

        # Update Mapped Values
        for mapping in scene.midi_mappings:
            if mapping.midi_cc == m_id:
                if mapping.use_note:
                    # Note Mode: ON=1.0, OFF=0.0
                    if m_type == 'Note': 
                        mapping.target_value = 1.0 if m_val > 0 else 0.0
                else:
                    # CC Mode: Normalize 0-127 to 0.0-1.0
                    if m_type != 'Note': 
                        mapping.target_value = m_val / 127.0

    # 2. ANIMATION & SMOOTHING LOOP (Runs every frame)
    for i, mapping in enumerate(scene.midi_mappings):
        if i not in animation_states: animation_states[i] = mapping.target_value
        
        current = animation_states[i]
        target = mapping.target_value
        
        # Smooth interpolation
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
        
        # Optimization: Only write to Blender if value changed 
        # OR if we are using continuous modes (Motor/Time)
        is_continuous = any(t.drive_mode == 'ACCUMULATE' or 'time' in t.expression or 'frame' in t.expression for t in mapping.targets)
        
        if abs(current - animation_states[i]) > 0.00001 or (is_continuous and abs(current) > 0.001):
            animation_states[i] = current
            
            # Apply Easing Curve
            curved_val = apply_easing(current, mapping.easing_mode)
            
            # Update all targets for this mapping
            for t in mapping.targets:
                apply_to_blender(t, curved_val, mapping.use_absolute)

    # Force Viewport Redraw
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()
            
    return 0.016 # Run at ~60fps

def start_listening(port_name):
    global midi_input, is_connected
    try:
        stop_listening()
        # Open port in Polling Mode (no callback function)
        midi_input = mido.open_input(port_name) 
        is_connected = True
        
        # Register the polling timer
        if not bpy.app.timers.is_registered(process_midi_events):
            bpy.app.timers.register(process_midi_events)
        return True
    except: is_connected = False; return False

def stop_listening():
    global midi_input, is_connected
    if midi_input: midi_input.close(); midi_input = None
    is_connected = False
    if bpy.app.timers.is_registered(process_midi_events): 
        bpy.app.timers.unregister(process_midi_events)

# ==============================================================================
# SECTION 4: UI & PROPERTIES
# ==============================================================================

@persistent
def auto_select_device(dummy):
    """Auto-connects to the first available MIDI device on startup."""
    try:
        devices = mido.get_input_names()
        if devices and bpy.context.scene.midi_device_name == "":
            bpy.context.scene.midi_device_name = devices[0]
            bpy.context.scene.midi_device_enum = devices[0]
    except: pass

def get_midi_devices(self, context):
    items = []
    try:
        devices = mido.get_input_names()
        for dev in devices: items.append((dev, dev, "MIDI Device"))
        if not items: items.append(('NONE', "No Devices Found", ""))
    except Exception as e: items.append(('ERROR', str(e), ""))
    return items

def update_device_selection(self, context):
    if self.midi_device_enum not in ['NONE', 'ERROR']:
        self.midi_device_name = self.midi_device_enum

class MidiTarget(bpy.types.PropertyGroup):
    data_path: bpy.props.StringProperty(name="Path", description="Right-click property > Copy Full Data Path")
    min_value: bpy.props.FloatProperty(name="Min", default=0.0)
    max_value: bpy.props.FloatProperty(name="Max", default=1.0)
    current_val_display: bpy.props.FloatProperty(default=0.0, options={'SKIP_SAVE'})
    drive_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[('SET', "Set (Standard)", ""), ('ACCUMULATE', "Motor (Accumulate)", "")],
        default='SET'
    )
    expression: bpy.props.StringProperty(name="Math", description="e.g: sin(time * x). Vars: x, time, frame")

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
        items=[
            ('LINEAR', "Linear", ""), 
            ('QUAD_IN', "Quad In", ""), 
            ('QUAD_OUT', "Quad Out", ""),
            ('QUAD_INOUT', "Quad Smooth", ""), 
            ('CUBIC_OUT', "Cubic", ""), 
            ('EXPO_OUT', "Expo", ""),
            ('BACK_OUT', "Back", ""), 
            ('ELASTIC_OUT', "Elastic", ""), 
            ('BOUNCE_OUT', "Bounce", "")
        ],
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
        
        # Connection Box
        box = layout.box()
        row = box.row(align=True)
        if not is_connected: row.prop(scene, "midi_device_enum", text="")
        else: row.label(text=f"Connected: {scene.midi_device_name}", icon='CHECKBOX_HLT')
        
        if not is_connected: row.operator("wm.midi_connect", text="Connect", icon='PLAY') 
        else: row.operator("wm.midi_disconnect", text="Disconnect", icon='PAUSE')

        # Monitor Box
        box = layout.box()
        if not is_connected: box.label(text="Disconnected", icon='CANCEL') 
        elif scene.monitor_id == -1: box.label(text="Monitor: Waiting...", icon='SOUND')
        else: box.label(text=f"Mon: {scene.monitor_type} #{scene.monitor_id} ({int(scene.monitor_val)})", icon='SOUND')

        # IO Buttons
        row = box.row(align=True)
        row.operator("wm.midi_export_json", text="Export", icon='EXPORT')
        row.operator("wm.midi_import_json", text="Import", icon='IMPORT')

        # Mappings List
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
                    
                    # Smart Paste
                    paste_op = t_row.operator("wm.midi_paste_target", text="", icon='PASTEDOWN')
                    paste_op.mapping_index = index
                    paste_op.target_index = t_index
                    
                    t_row.operator("wm.midi_remove_target", text="", icon='REMOVE').mapping_index = index
                    
                    t_row = t_box.row(align=True)
                    t_row.prop(target, "drive_mode", text="")
                    if target.expression: t_row.prop(target, "expression", text="Expr", icon='SCRIPT')
                    else:
                        if not mapping.use_absolute:
                            t_row.prop(target, "min_value", text="Min")
                            t_row.prop(target, "max_value", text="Max")
                        else: t_row.label(text="(Abs Mode)")
                    
                    if target.expression: t_box.prop(target, "expression", text="Math")
                    else: t_box.prop(target, "expression", text="Add Math...")

                col.operator("wm.midi_add_target", text="Add Target", icon='ADD').mapping_index = index
        layout.operator("wm.midi_add_mapping", text="New Mapping", icon='ADD')

# ==============================================================================
# SECTION 5: OPERATORS & REGISTRATION
# ==============================================================================

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
        return {'FINISHED'}

class MIDI_OT_AddMapping(bpy.types.Operator):
    bl_idname = "wm.midi_add_mapping"
    bl_label = "Add"
    def execute(self, context):
        new_map = context.scene.midi_mappings.add()
        
        # AUTO-PICK FEATURE:
        # Uses the last detected MIDI input (monitor_id) to auto-fill the new mapping.
        if context.scene.monitor_id != -1:
            new_map.midi_cc = context.scene.monitor_id
            new_map.use_note = (context.scene.monitor_type == 'Note')
            new_map.name = f"{context.scene.monitor_type} {context.scene.monitor_id}"
            
        new_map.targets.add()
        return {'FINISHED'}

class MIDI_OT_DuplicateMapping(bpy.types.Operator):
    bl_idname = "wm.midi_duplicate_mapping"
    bl_label = "Dup"
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
    bl_label = "Rem"
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
    bl_label = "Rem Target"
    mapping_index: bpy.props.IntProperty()
    def execute(self, context):
        targets = context.scene.midi_mappings[self.mapping_index].targets
        if len(targets) > 0: targets.remove(len(targets)-1)
        return {'FINISHED'}

class MIDI_OT_PasteTarget(bpy.types.Operator):
    bl_idname = "wm.midi_paste_target"
    bl_label = "Paste"
    mapping_index: bpy.props.IntProperty()
    target_index: bpy.props.IntProperty()
    def execute(self, context):
        path = context.window_manager.clipboard.strip()
        target = context.scene.midi_mappings[self.mapping_index].targets[self.target_index]
        if path.startswith("bpy."): target.data_path = path
        else:
            obj = context.active_object
            if obj: target.data_path = f'bpy.data.objects["{obj.name}"].{path}'
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
                "abs": m.use_absolute, "speed": m.smooth_speed, 
                "curve": m.easing_mode, "targets": targets_data
            })
        with open(self.filepath, 'w') as f: json.dump(data, f, indent=4)
        return {'FINISHED'}

class MIDI_OT_ImportJSON(bpy.types.Operator, ImportHelper):
    bl_idname = "wm.midi_import_json"
    bl_label = "Import"
    filename_ext = ".json"
    def execute(self, context):
        try:
            with open(self.filepath, 'r') as f: data = json.load(f)
        except: return {'CANCELLED'}
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
                nt.data_path = t_data.get("path", "")
                nt.min_value = t_data.get("min", 0.0)
                nt.max_value = t_data.get("max", 1.0)
                nt.drive_mode = t_data.get("mode", 'SET')
                nt.expression = t_data.get("expr", "")
        return {'FINISHED'}

classes = (MidiTarget, MidiMapping, OBJECT_PT_MidiController, MIDI_OT_Connect, MIDI_OT_Disconnect, MIDI_OT_AddMapping, MIDI_OT_DuplicateMapping, MIDI_OT_RemoveMapping, MIDI_OT_AddTarget, MIDI_OT_RemoveTarget, MIDI_OT_PasteTarget, MIDI_OT_ExportJSON, MIDI_OT_ImportJSON)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.midi_mappings = bpy.props.CollectionProperty(type=MidiMapping)
    bpy.types.Scene.midi_device_name = bpy.props.StringProperty()
    bpy.types.Scene.midi_device_enum = bpy.props.EnumProperty(items=get_midi_devices, update=update_device_selection)
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