# SimpleMIDI for Blender

A simple MIDI controller addon for Blender. It allows you to map MIDI knobs/sliders/keys to any property in Blender (Object transforms, Modifier values, Colors, etc.).

## Installation

1. Download the release `.zip`.
2. Open Blender.
3. Go to **Edit > Preferences > Get Extensions**.
4. Click the **Disk Icon (▼)** > **Install from Disk...** and select the zip.

## Usage

### Basic Mapping
1. Connect your MIDI Device.
2. In the 3D Viewport sidebar (press `N`), click the **Midi** tab.
3. Click **Connect**. Move a knob on your controller to see the "Monitor" value change.
4. Click **New Mapping**.
5. **Right-Click** any property in Blender (e.g., Cube Location X) and select **Copy Full Data Path**.
6. Click the **Paste (📋)** button in the addon panel.

### Advanced: controlling Geometry Nodes
Geometry Nodes inputs are dynamic, so you cannot copy their path from the Modifier tab directly.
1. In the Modifier tab, click the **"Move to Nodes"** icon next to the value you want to control.
2. Open the **Geometry Node Editor**.
3. Locate the **Group Input** node.
4. **Right-Click** the specific socket input and select **Copy Full Data Path**.
5. Paste this path into SimpleMIDI.

## Modes

### 1. Set (Standard)
Maps the knob directly to the value.
* **Knob 0%** = Min Value
* **Knob 100%** = Max Value
* *Best for: Opacity, Scale, Rotation, Colors.*

### 2. Motor (Accumulate)
Treats the input as a "Gas Pedal" or Velocity.
* **Knob at 0** = Stop changing.
* **Knob at Max** = Increase value at Max Speed.
* **Setup:** Set `Min: 0.0` and `Max: 0.1` (Adjust Max for top speed).
* *Best for: Endless rotation, Driving a car forward, Scrolling timeline.*

## Math Expressions

You can transform the input signal using math expressions. This is useful for inverting knobs, changing ranges, or creating procedural motion driven by time.

### Available Variables
* `x` : The raw MIDI input value (0.0 to 1.0).
* `time` : Current scene time in seconds.
* `frame` : Current scene frame number.

### Supported Functions
Only the following math functions are allowed:
* **Basic:** `+`, `-`, `*`, `/`, `**` (Power)
* **Trig:** `sin`, `cos`, `tan`, `pi`
* **Utilities:** `min`, `max`, `abs`, `round`, `sqrt`, `pow`

### Examples

#### 🎛️ Knobs & Sliders (Continuous `0.0` to `1.0`)
| Effect | Expression | Description |
| :--- | :--- | :--- |
| **Increase Range** | `x * 10` | Outputs 0 to 10 (Good for Arrays/Counts). |
| **Invert** | `1.0 - x` | Turning knob right decreases value. |
| **Exponential** | `pow(x, 3)` | Fine control at low values, fast at high values. |
| **Stepped (Snap)** | `round(x * 4) / 4` | Snaps value to 0, 0.25, 0.5, 0.75, 1.0. |
| **Auto-Wiggle** | `sin(time * 5) * x` | Object moves automatically; Knob controls intensity. |
| **Speed Control** | `sin(time * x * 10)` | Object moves -1 to 1; Knob controls frequency. |

#### 🎹 Buttons & Keys (Binary `0.0` or `1.0`)
| Effect | Expression | Description |
| :--- | :--- | :--- |
| **Momentary Switch** | `x` | **Pressed** = On (1), **Released** = Off (0). Great for checkboxes. |
| **Hold to Hide** | `1.0 - x` | **Pressed** = Off (0), **Released** = On (1). Good for "Mute". |
| **Jump to Value** | `x * 45` | **Pressed** = 45, **Released** = 0. Good for rotation snaps. |
| **Value A / Value B** | `5 + (x * 10)` | **Released** = 5, **Pressed** = 15. Toggles between two specific numbers. |
| **Motor Drive** | `x * 0.1` | *(Use Accumulate Mode)* **Hold** to rotate/move object, **Release** to stop. |
| **Camera Cut** | `x * 2` | **Pressed** = Camera Index 2, **Released** = Camera Index 0. |