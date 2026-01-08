import bpy
import queue
import time

# Thread-safe queue for incoming MIDI signals
execution_queue = queue.SimpleQueue()

# Dictionary to store the current animated state of each mapping
# Key: Mapping Index, Value: Current Normalized Value (0.0 to 1.0)
animation_states = {}

# --- Easing Functions ---
def apply_easing(t, mode):
    """ Transforms linear 0-1 progress into a curve """
    if t < 0: t = 0
    if t > 1: t = 1
    
    if mode == 'LINEAR': return t
    elif mode == 'EASE_IN': return t * t
    elif mode == 'EASE_OUT': return t * (2 - t)
    elif mode == 'SMOOTHSTEP': return t * t * (3 - 2 * t)
    return t

# --- Path Resolving ---
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
    except Exception:
        return None, None, None

def apply_to_blender(mapping, normalized_value):
    """ 
    Takes a 0.0-1.0 value, applies Min/Max scaling, and writes to Blender 
    """
    obj, prop_name, index = resolve_path(mapping.data_path)
    if obj is None: return

    # Scale 0-1 to Min-Max
    final_val = mapping.min_value + (normalized_value * (mapping.max_value - mapping.min_value))

    try:
        if index != -1:
            current_vector = getattr(obj, prop_name)
            current_vector[index] = final_val
        else:
            setattr(obj, prop_name, final_val)
    except Exception:
        pass

# --- MIDI Backend ---
def midi_callback(message):
    """ Background thread: Just capture the raw signal """
    msg_type = message.type
    if msg_type == 'control_change':
        execution_queue.put(('CC', message.control, message.value))
    elif msg_type == 'note_on':
        execution_queue.put(('Note', message.note, message.velocity))
    elif msg_type == 'note_off':
        execution_queue.put(('Note', message.note, 0))

def queue_processor():
    """ 
    Main Thread Animation Loop (~60 FPS)
    Handles both MIDI input processing AND Frame Interpolation.
    """
    scene = bpy.context.scene
    
    # 1. Process New MIDI Inputs -> Update Targets
    while not execution_queue.empty():
        m_type, m_id, m_val = execution_queue.get()
        
        # Update Monitor
        try:
            scene.monitor_type = m_type
            scene.monitor_id = m_id
            scene.monitor_val = float(m_val)
        except: pass

        # Update Mappings Targets
        for i, mapping in enumerate(scene.midi_mappings):
            if mapping.midi_cc == m_id:
                # Determine Target (0.0 to 1.0)
                target = 0.0
                
                if mapping.use_note:
                    # For Keys: Note On = 1.0, Note Off = 0.0
                    # We ignore CC signals if in Key Mode
                    if m_type == 'Note':
                        target = 1.0 if m_val > 0 else 0.0
                        mapping.target_value = target
                else:
                    # For Knobs: Map 0-127 to 0.0-1.0
                    # We ignore Note signals if in Knob Mode
                    if m_type != 'Note':
                        target = m_val / 127.0
                        mapping.target_value = target

    # 2. Animation Step (Interpolate Current -> Target)
    # This runs every frame for smooth movement
    
    for i, mapping in enumerate(scene.midi_mappings):
        # Initialize state if missing
        if i not in animation_states:
            animation_states[i] = mapping.target_value
            
        current = animation_states[i]
        target = mapping.target_value
        
        # If we are effectively "there", skip math to save CPU
        if abs(current - target) < 0.001:
            animation_states[i] = target
            current = target
        else:
            # Interpolation Logic
            speed = mapping.smooth_speed
            
            if speed >= 1.0:
                # Instant (No smoothing)
                current = target
            else:
                # Move towards target
                # Adjust delta for frame rate (approx 60fps)
                # Lower speed = Slower transition
                step = speed * 0.2 
                
                if current < target:
                    current += step
                    if current > target: current = target
                else:
                    current -= step
                    if current < target: current = target
        
        animation_states[i] = current
        
        # 3. Apply Curve to the Current Interpolated Value
        # This allows the "Ease In" to apply to the time transition
        curved_value = apply_easing(current, mapping.easing_mode)
        
        # 4. Write to Blender
        apply_to_blender(mapping, curved_value)

    # Force Redraw (Keep UI responsive)
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D' or area.type == 'PROPERTIES':
                area.tag_redraw()

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
    except Exception:
        return False

def stop_listening():
    global midi_input
    if 'midi_input' in globals() and midi_input:
        midi_input.close()
        midi_input = None
    if bpy.app.timers.is_registered(queue_processor):
        bpy.app.timers.unregister(queue_processor)