import bpy
import queue

# Queue to send data from MIDI thread -> Blender thread
execution_queue = queue.SimpleQueue()

# Monitor variables (What was the last thing touched?)
last_active_type = "None"  # "CC" or "Note"
last_active_id = -1        # The CC number or Note number
last_active_val = 0.0      # The velocity or value

def resolve_path(path):
    """ Safely resolves a Blender data path. """
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

def apply_midi_value(mapping, midi_value, is_note_event):
    """ 
    Applies the value. 
    If it's a Note: Note On = Max, Note Off = Min.
    If it's a CC: value scales 0-127 between Min/Max.
    """
    obj, prop_name, index = resolve_path(mapping.data_path)
    if obj is None: return

    # Logic for Notes (Buttons) vs CC (Knobs)
    if mapping.use_note:
        if not is_note_event: return # Ignore CC signals if this is mapped to a Note
        
        # If velocity > 0, it's a press (Max), else it's release (Min)
        target_val = mapping.max_value if midi_value > 0 else mapping.min_value
    
    else:
        if is_note_event: return # Ignore Notes if this is mapped to a Knob
        
        # Standard Knob Logic (0-127)
        factor = midi_value / 127.0
        target_val = mapping.min_value + (factor * (mapping.max_value - mapping.min_value))

    # Apply to Blender
    try:
        if index != -1:
            current_vector = getattr(obj, prop_name)
            current_vector[index] = target_val
        else:
            setattr(obj, prop_name, target_val)
    except Exception:
        pass

def midi_callback(message):
    """ Called by background thread. Listens for EVERYTHING now. """
    global last_active_type, last_active_id, last_active_val
    
    msg_type = message.type
    
    # Handle Knobs (CC)
    if msg_type == 'control_change':
        last_active_type = "CC"
        last_active_id = message.control
        last_active_val = message.value
        execution_queue.put(('CC', message.control, message.value))
        
    # Handle Keys (Notes)
    elif msg_type == 'note_on':
        last_active_type = "Note"
        last_active_id = message.note
        last_active_val = message.velocity
        execution_queue.put(('Note', message.note, message.velocity))
        
    elif msg_type == 'note_off':
        last_active_type = "Note"
        last_active_id = message.note
        last_active_val = 0 # Note off = 0 velocity
        execution_queue.put(('Note', message.note, 0))

def queue_processor():
    """ Runs on Main Thread (~60fps) """
    # 1. Update Monitor UI variables
    try:
        bpy.types.Scene.monitor_type = last_active_type
        bpy.types.Scene.monitor_id = last_active_id
        bpy.types.Scene.monitor_val = last_active_val
    except: pass

    # 2. Process actions
    while not execution_queue.empty():
        m_type, m_id, m_val = execution_queue.get()
        
        if bpy.context.scene:
            for mapping in bpy.context.scene.midi_mappings:
                # Check if this mapping matches the ID (Note# or CC#)
                if mapping.midi_cc == m_id:
                    # Pass the event type so we know if it's a Note or CC
                    is_note = (m_type == 'Note')
                    apply_midi_value(mapping, m_val, is_note)
    
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