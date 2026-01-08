import bpy
import queue
import math

# Thread-safe queue
execution_queue = queue.SimpleQueue()
animation_states = {}

# --- Easing Functions ---
def apply_easing(t, mode):
    if t < 0: t = 0
    if t > 1: t = 1
    if mode == 'LINEAR': return t
    elif mode == 'QUAD_IN': return t * t
    elif mode == 'QUAD_OUT': return 1 - (1 - t) * (1 - t)
    elif mode == 'QUAD_INOUT': return 2 * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 2) / 2
    elif mode == 'CUBIC_OUT': return 1 - math.pow(1 - t, 3)
    elif mode == 'EXPO_OUT': return 1 if t == 1 else 1 - math.pow(2, -10 * t)
    elif mode == 'BACK_OUT':
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * math.pow(t - 1, 3) + c1 * math.pow(t - 1, 2)
    elif mode == 'ELASTIC_OUT':
        if t == 0: return 0
        if t == 1: return 1
        c4 = (2 * math.pi) / 3
        return math.pow(2, -10 * t) * math.sin((t * 10 - 0.75) * c4) + 1
    elif mode == 'BOUNCE_OUT':
        n1 = 7.5625
        d1 = 2.75
        if t < 1 / d1: return n1 * t * t
        elif t < 2 / d1: t -= 1.5 / d1; return n1 * t * t + 0.75
        elif t < 2.5 / d1: t -= 2.25 / d1; return n1 * t * t + 0.9375
        else: t -= 2.625 / d1; return n1 * t * t + 0.984375
    return t

def resolve_path(path):
    try:
        if not path.startswith("bpy."): return None, None, None
        parts = path.split('.')
        obj = bpy
        for part in parts[1:-1]:
            if '[' in part and ']' in part:
                base_name, key_section = part.split('[', 1)
                key = key_section.rstrip(']').strip("'\"")
                if key.isdigit(): key = int(key)
                obj = getattr(obj, base_name)[key]
            else:
                obj = getattr(obj, part)
        last_part = parts[-1]
        index = -1
        if '[' in last_part and ']' in last_part:
            prop_name, idx_section = last_part.split('[', 1)
            index = int(idx_section.rstrip(']'))
        else:
            prop_name = last_part
        return obj, prop_name, index
    except Exception: return None, None, None

def apply_to_blender(mapping, normalized_value):
    obj, prop_name, index = resolve_path(mapping.data_path)
    
    # --- LOGIC CHECK ---
    if mapping.use_absolute:
        # ABSOLUTE MODE: 
        # normalized_value is 0.0 to 1.0. 
        # Multiply by 127 to get the raw MIDI value (e.g. 64.0, 127.0).
        final_val = normalized_value * 127.0
    else:
        # STANDARD MODE:
        # Scale between User Min and Max
        final_val = mapping.min_value + (normalized_value * (mapping.max_value - mapping.min_value))
    
    if obj is None: return

    try:
        if index != -1:
            current_vector = getattr(obj, prop_name)
            current_vector[index] = final_val
        else:
            setattr(obj, prop_name, final_val)
    except Exception: pass

def midi_callback(message):
    msg_type = message.type
    if msg_type == 'control_change':
        execution_queue.put(('CC', message.control, message.value))
    elif msg_type == 'note_on':
        execution_queue.put(('Note', message.note, message.velocity))
    elif msg_type == 'note_off':
        execution_queue.put(('Note', message.note, 0))

def queue_processor():
    scene = bpy.context.scene
    
    # 1. Process Inputs
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
                    if m_type == 'Note':
                        mapping.target_value = 1.0 if m_val > 0 else 0.0
                else:
                    if m_type != 'Note':
                        # Always normalize to 0-1 first for consistent animation math
                        mapping.target_value = m_val / 127.0

    # 2. Animation Loop
    for i, mapping in enumerate(scene.midi_mappings):
        if i not in animation_states: animation_states[i] = mapping.target_value
        
        current = animation_states[i]
        target = mapping.target_value
        
        # Optimization: Stop calculating if we are close enough
        if abs(current - target) < 0.001:
            current = target
        else:
            speed = mapping.smooth_speed
            # Calculate step based on speed (non-linear feel)
            step = speed * 0.2 if speed < 1.0 else 1.0
            
            if current < target:
                current += step
                if current > target: current = target
            else:
                current -= step
                if current < target: current = target
        
        animation_states[i] = current
        
        # Apply Curve -> Then Apply to Blender (which handles the Abs/MinMax scaling)
        curved_value = apply_easing(current, mapping.easing_mode)
        apply_to_blender(mapping, curved_value)

    # 3. Force UI Redraw
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()

    return 0.016 

def start_listening(port_name):
    import mido
    try:
        stop_listening()
        global midi_input
        midi_input = mido.open_input(port_name, callback=midi_callback)
        if not bpy.app.timers.is_registered(queue_processor):
            bpy.app.timers.register(queue_processor)
        return True
    except Exception: return False

def stop_listening():
    global midi_input
    if 'midi_input' in globals() and midi_input:
        midi_input.close()
        midi_input = None
    if bpy.app.timers.is_registered(queue_processor):
        bpy.app.timers.unregister(queue_processor)