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
You can transform the input using Python math.
* `x` = Input value (0.0 to 1.0)
* `time` = Current scene time (seconds)
* `sin(time * x)` = Oscillate speed based on knob input.