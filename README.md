# PyLifter

PyLifter is an open-source Python library and toolkit for controlling **MyLifter** motorized winch systems via Bluetooth Low Energy (BLE). It was developed using the official MyLifter Android app as a reference for the BLE protocol.

## Features

- **Direct Control**: Move the winch UP, DOWN, and STOP programmatically.
- **Interactive CLI**: A robust terminal-based interface with **Live Status Display** for real-time monitoring.
- **Calibrated Units**: Supports linear calibration to convert internal position units to real-world centimeters.
- **Soft Limit Override**: Implements the "Override" functionality to bypass soft limits when necessary.
- **Robust Error Handling**: Automatically handles and clears device errors (like Sync Error 9) to ensure reliable operation.
- **Configuration Persistence**: Saves pairing keys and calibration data to `pylifter_config.json` for seamless reconnection.

## Requirements

- Python 3.9+
- `bleak` (Bluetooth Low Energy library)
- Linux (tested) or other OS with BLE support.

## installation

Clone the repository:
```bash
git clone https://github.com/sblu/PyLifter.git
cd PyLifter
```

Install dependencies:
```bash
pip install bleak
```

## Quick Start

### 1. Interactive Control & Pairing
The main way to use this library is via the interactive demo. This is also used to pair your devices.

```bash
python3 PyLifter/winch_demo_interactive.py
```

To enable verbose logging to a file (`debug.log`), use the `--debug` flag:
```bash
python3 PyLifter/winch_demo_interactive.py --debug
```

**Initial Pairing:**
If no devices are configured, the script will prompt you to enter **Pairing Mode**.
1.  Type `PAIR` in the command prompt.
2.  Scan for devices and select your winch.
3.  **Important:** You must **physically press the button on the MyLifter unit** when prompted to authorize pairing and retrieve the secure Passkey.

Once paired, the passkey is saved to `pylifter_config.json`, and future connections will be automatic.

**Commands:**
- `U 10 [speed]`: Move UP by 10 cm (optional speed 25-100%).
- `D 5.5 [speed]`: Move DOWN by 5.5 cm (optional speed 25-100%).
- `LIFT`: Smart Lift (Move UP to High Limit).
- `LOWER`: Smart Lower (Move DOWN to Low Limit).
- `SH`: Set **High** (Top) Soft Limit at current position.
- `SL`: Set **Low** (Bottom) Soft Limit at current position.
- `?`: Show help menu.
- `Q`: Quit.

### 2. Calibration (Optional but Recommended)
To enable control using real-world units (cm) instead of raw motor steps, run the calibration script **after pairing**:

```bash
python3 PyLifter/calibrate_units.py
```
Follow the on-screen instructions to measure top and bottom positions. This will save a `slope` and `intercept` to your config file.

### 3. Multi-Winch Control
The interactive demo supports controlling multiple winches simultaneously.

**Pairing New Winches:**
Use the `PAIR` command to scan for and add new winches to your configuration.
1. Type `PAIR`.
2. Select the new winch from the list.
3. Press the button on the winch when prompted.

**Targeted Commands:**
You can specific which winch(es) to control by prefixing the command with IDs (comma-separated).
*   `1 U 10`: Move Winch 1 UP by 10 cm.
*   `ALL D 20 50`: Move ALL winches DOWN by 20 cm at **50% speed**.
*   `2 LIFT`: Smart Lift Winch 2.
*   `1,2 SH`: Set High Limit for Winches 1 and 2.
*   `ALL LIFT`: Smart Lift **ALL** connected winches.

If no ID is specified (e.g., just `U 10`), the command applies to **Winch 1** by default.

**Unpairing & Renumbering:**
Use `UNPAIR` to remove a winch. The system will automatically renumber remaining winches to fill gaps (e.g., deleting ID 1 makes ID 2 become the new ID 1).

**Soft Limits:**
If the winch hits a Soft Limit (Error 0x81), the script will pause and ask if you want to override. Type `Y` to proceed past the limit.


## Firmware Compatibility

- **Verification**: The library checks the connected winch firmware active startup. If an untested version is detected (anything other than **v3.2**), a warning will be displayed. You can choose to bypass this at your own risk.
- **Updates**: This library **does not** support updating the winch firmware. Please use the official MyLifter app for firmware updates.
- **Tested Version**: Validated against Firmware **v3.2**. Compatible with v3.1 with limited testing.

## Project Structure

- `PyLifter/pylifter/`: Core library package (`client.py`, `protocol.py`).
- `PyLifter/winch_demo_interactive.py`: Main user interface.
- `PyLifter/calibrate_units.py`: Calibration utility.
- [`PROTOCOL.md`](PyLifter/PROTOCOL.md): Technical documentation of the reverse-engineered BLE protocol.

## Disclaimer

This software is not affiliated with or endorsed by the creators of MyLifter. Use at your own risk. Incorrect usage of motorized winches can cause physical damage or injury.
