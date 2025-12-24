
import asyncio
import logging
import json
import curses
from pylifter import PyLifterClient, MoveCode, SmartPointCode

# Configure logs to be minimal
logging.basicConfig(level=logging.WARNING)

async def monitor_move(client: PyLifterClient, target_pos: int, direction: MoveCode):
    """
    Moves the winch in 'direction' until 'target_pos' is reached.
    Assumes negative slope (more negative pos = larger distance).
    UP = increasing pos (towards 0), DOWN = decreasing pos (towards -inf).
    """
    
    # Safety Check
    start_pos = client._last_known_position
    if start_pos is None:
        print("Error: Unknown start position.")
        return

    print(f"  -> Moving {'UP' if direction == MoveCode.UP else 'DOWN'} to Target Pos: {target_pos}")
    
    # CRITICAL Fix: Clear any existing End-of-Travel errors before moving
    await client.clear_error()
    await asyncio.sleep(0.25)
    
    await client.move(direction, speed=100)
    
    is_overridden = False
    
    try:
        while True:
            current_pos = client._last_known_position
            dist = client.current_distance
            
            print(f"     Pos: {current_pos:<6} | Dist: {dist:.1f} cm", end='\r')
            
            # Check Condition
            if direction == MoveCode.UP:
                # Moving towards 0 (increasing). Stop if current >= target.
                if current_pos >= target_pos:
                    break
                # Safety: If we hit top (0x86 error would handle this mostly, but good to check status)
                if client.last_error_code == 0x86:
                     print("\n  -> Reached Top Limit (0x86).")
                     break
                if client.last_error_code == 0x81 and not is_overridden:
                     print("\n  -> Reached Soft Limit (0x81).")
                     await client.stop()
                     
                     # Prompt for Override
                     print(f"     [!] Condition Met. Override Limit? (Y/N): ", end='', flush=True)
                     # We need to run input in executor to avoid blocking loop
                     resp = await asyncio.get_event_loop().run_in_executor(None, input)
                     if resp.strip().upper().startswith('Y'):
                         print("     -> Sending Override...")
                         await client.clear_error() # Critical fix for Limit error
                         await client.go_override()
                         await asyncio.sleep(0.5)
                         # Resume Move with OVERRIDE direction
                         override_dir = MoveCode.OVERRIDE_UP if direction == MoveCode.UP else MoveCode.OVERRIDE_DOWN
                         await client.move(override_dir, speed=100)
                         is_overridden = True
                         continue
                     else:
                         print("     -> Stopping.")
                         break
            else:
                # Moving towards -inf (decreasing). Stop if current <= target.
                if current_pos <= target_pos:
                    break
                if client.last_error_code == 0x86:
                     print("\n  -> Reached Bottom Limit (0x86).")
                     break
                if client.last_error_code == 0x81 and not is_overridden:
                     print("\n  -> Reached Soft Limit (0x81).")
                     await client.stop()
                     
                     # Prompt for Override
                     print(f"     [!] Condition Met. Override Limit? (Y/N): ", end='', flush=True)
                     resp = await asyncio.get_event_loop().run_in_executor(None, input)
                     if resp.strip().upper().startswith('Y'):
                         print("     -> Sending Override...")
                         await client.clear_error() # Critical fix for Limit error
                         await client.go_override()
                         await asyncio.sleep(0.5)
                         # Resume Move with OVERRIDE direction
                         override_dir = MoveCode.OVERRIDE_UP if direction == MoveCode.UP else MoveCode.OVERRIDE_DOWN
                         await client.move(override_dir, speed=100)
                         is_overridden = True
                         continue
                     else:
                         print("     -> Stopping.")
                         break
            
            await asyncio.sleep(0.05)
            
    finally:
        await client.stop()
        print() # Newline after \r loop
        await asyncio.sleep(0.5) # Settle
        print(f"  -> Stopped at: {client._last_known_position} | {client.current_distance:.1f} cm")

import os

async def main():
    # Resolve config path relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "pylifter_config.json")
    
    # Defaults
    mac_address = "CC:CC:CC:FE:15:33"
    passkey = None
    calibration = {}

    # 1. Load Config
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
            mac_address = config.get("mac_address", mac_address)
            passkey = config.get("passkey")
            calibration = config.get("calibration", {})
            print(f"[Config] Loaded config for {mac_address}")
    except FileNotFoundError:
        print("[Config] No config file found. Using defaults.")

    client = PyLifterClient(mac_address, passkey=passkey)
    
    # Apply Calibration
    # We also need these values locally for inverse calculation
    slope = 0.0
    intercept = 0.0
    if "slope" in calibration and "intercept" in calibration:
        slope = calibration["slope"]
        intercept = calibration["calibration"] if "calibration" in calibration else 0 # Wait, bug in previous code? No.
        # calibration is dict. intercept is key.
        intercept = calibration["intercept"]
        client.set_unit_calibration(slope, intercept)
    else:
        print("Warning: No calibration found. Commands will assume Units=CM (which is wrong).")
        slope = 1.0
        intercept = 0.0
        client.set_unit_calibration(1.0, 0.0)

    try:
        print("\n--- PyLifter Interactive Control ---\n")
        print("1. Connecting...")
        await client.connect()
        print("   Connected.")
        
        # Save passkey if updated
        if client.passkey and client.passkey != passkey:
             config["passkey"] = client.passkey.hex() if isinstance(client.passkey, bytes) else client.passkey
             with open(config_file, "w") as f:
                 json.dump(config, f, indent=4)
        
        # Interactive Loop
        while True:
            # Show current state
            print(f"\nCurrent: Pos={client._last_known_position} | Dist={client.current_distance:.1f} cm")
            cmd_str = await asyncio.get_event_loop().run_in_executor(None, input, "Command (U/D/SH/SL/Q/?): ")
            
            parts = cmd_str.strip().split()
            if not parts: continue
            
            cmd = parts[0].upper()
            
            if cmd == 'Q':
                break

            if cmd == '?':
                print("\n--- Command Help ---")
                print("  U <val> : Move UP by <val> centimeters (e.g., 'U 10')")
                print("  D <val> : Move DOWN by <val> centimeters (e.g., 'D 5.5')")
                print("  SH      : Set HIGH (Top) Soft Limit at current position")
                print("  SL      : Set LOW (Bottom) Soft Limit at current position")
                print("  Q       : Quit")
                print("  ?       : Show this help message")
                print("--------------------\n")
                continue

            if cmd == 'SH':
                print("   -> Setting High (Top) Limit...")
                await client.set_smart_point(SmartPointCode.TOP)
                print("   -> High Limit Set.")
                continue

            if cmd == 'SL':
                print("   -> Setting Low (Bottom) Limit...")
                await client.set_smart_point(SmartPointCode.BOTTOM)
                print("   -> Low Limit Set.")
                continue
                
            if cmd in ['U', 'D'] and len(parts) > 1:
                try:
                    delta_cm = float(parts[1])
                    current_dist = client.current_distance
                    
                    if cmd == 'U':
                        # Retract: Decrease distance
                        target_dist = current_dist - delta_cm
                        direction = MoveCode.UP
                    else:
                        # Extend: Increase distance
                        target_dist = current_dist + delta_cm
                        direction = MoveCode.DOWN
                        
                    # Calculate Target Position
                    # Dist = Slope * Pos + Intercept
                    # Pos = (Dist - Intercept) / Slope
                    if slope == 0:
                        print("Error: Calibration slope is 0.")
                        continue
                        
                    target_pos = int((target_dist - intercept) / slope)
                    
                    await monitor_move(client, target_pos, direction)
                    
                except ValueError:
                    print("Invalid distance.")
            else:
                print("Unknown command. Type '?' for help.")
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
    finally:
        print("\nDisconnecting...")
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
