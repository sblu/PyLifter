
import asyncio
import logging
from pylifter import PyLifterClient, MoveCode

# Configure logs to suppress library chatter
logging.basicConfig(level=logging.WARNING)

async def wait_for_input(prompt):
    return await asyncio.get_event_loop().run_in_executor(None, input, prompt)

async def move_until_stop(client, direction):
    """Moves in direction until End of Travel (0x86) or manual stop."""
    print(f"Moving {'UP' if direction == MoveCode.UP else 'DOWN'} to End of Travel...")
    
    await client.move(direction, speed=100)
    
    start_pos = client._last_known_position
    last_pos = start_pos
    stalled_count = 0
    
    try:
        while True:
            # Check for End of Travel Flag
            if client.last_error_code == 0x86:
                print(" -> Hit End of Travel (0x86).")
                break
            
            # Watchdog for stalling (in case flag is missed)
            current_pos = client._last_known_position
            if current_pos == last_pos:
                stalled_count += 1
                if stalled_count > 60: # 3 seconds no movement?
                    print(" -> Stalled (No position change). Stopping.")
                    break
            else:
                stalled_count = 0
                last_pos = current_pos
                
            await asyncio.sleep(0.05)
            
    finally:
        await client.stop()
        await asyncio.sleep(1.0) # Settle

import os

async def main():
    mac_address = "CC:CC:CC:FE:15:33" 
    # Resolve config path relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "pylifter_config.json")
    
    passkey_file = "passkey.txt"
    passkey = None
    
    try:
        with open(passkey_file, "r") as f: passkey = f.read().strip()
    except: pass

    client = PyLifterClient(mac_address, passkey=passkey)
    
    try:
        print("Connecting...")
        await client.connect()
        print("Connected.")
        
        # 1. Move UP to Zero
        print("\n--- Step 1/3: Calibrating ZERO (Top) ---")
        await move_until_stop(client, MoveCode.UP)
        pos1 = client._last_known_position
        print(f"Internal Position: {pos1}")
        dist1_str = await wait_for_input("Enter measured distance (cm) from winch to hook: ")
        dist1 = float(dist1_str)
        
        # 2. Move DOWN
        print("\n--- Step 2/3: Calibrating BOTTOM ---")
        print("Clearing errors and Moving DOWN for 3 seconds...")
        await client.clear_error()
        await asyncio.sleep(0.5)
        await client.move(MoveCode.DOWN, speed=100)
        await asyncio.sleep(3.0)
        await client.stop()
        await asyncio.sleep(1.0)
        
        pos2 = client._last_known_position
        print(f"Internal Position: {pos2}")
        dist2_str = await wait_for_input("Enter measured distance (cm) from winch to hook: ")
        dist2 = float(dist2_str)
        
        # 3. Move UP to check
        print("\n--- Step 3/3: Verifying ZERO ---")
        await move_until_stop(client, MoveCode.UP)
        pos3 = client._last_known_position
        print(f"Internal Position: {pos3}")
        dist3_str = await wait_for_input("Enter measured distance (cm) from winch to hook: ")
        dist3 = float(dist3_str)
        
        # Calculate
        delta_pos = abs(pos2 - pos1)
        delta_dist = abs(dist2 - dist1)
        
        if delta_dist == 0:
            print("Error: Distance delta is zero.")
            return

        units_per_cm = delta_pos / delta_dist
        cm_per_unit = delta_dist / delta_pos
        
        print(f"\n--- Results ---")
        print(f"Pos Delta: {delta_pos}")
        print(f"Dist Delta: {delta_dist} cm")
        print(f"Ratio: {units_per_cm:.2f} units/cm")
        print(f"Scale: {cm_per_unit:.5f} cm/unit")
        
        print(f"\n--- Validation Test ---")
        print("Clearing errors and Moving DOWN for 5 seconds...")
        await client.clear_error()
        await asyncio.sleep(0.5)
        await client.move(MoveCode.DOWN, speed=100)
        await asyncio.sleep(5.0)
        await client.stop()
        await asyncio.sleep(1.0)
        
        final_pos = client._last_known_position
        
        # Linear Regression (Two Point)
        # Point 1: (pos_avg_top, dist_avg_top)
        # Point 2: (pos2, dist2)
        pos_avg_top = (pos1 + pos3) / 2
        dist_avg_top = (dist1 + dist3) / 2
        
        # m = (y2 - y1) / (x2 - x1)
        if (pos2 - pos_avg_top) == 0:
             print("Error: Position delta is zero.")
             return

        slope = (dist2 - dist_avg_top) / (pos2 - pos_avg_top)
        intercept = dist_avg_top - (slope * pos_avg_top)
        
        # Test Calculation
        est_val_dist = (slope * final_pos) + intercept
        
        print(f"\n=== Calibration Summary (Linear) ===")
        print(f"Top Avg Pos: {pos_avg_top:.1f} | Top Avg Dist: {dist_avg_top:.1f} cm")
        print(f"Bot Pos:     {pos2} | Bot Dist:     {dist2} cm")
        print(f"---------------------------")
        print(f"Slope (m):     {slope:.6f} cm/unit")
        print(f"Intercept (b): {intercept:.2f} cm")
        print(f"Formula: Dist = ({slope:.6f} * Pos) + {intercept:.2f}")
        print(f"---------------------------")
        print(f"Validation Pos: {final_pos}")
        print(f"Estimated Dist: {est_val_dist:.2f} cm")
        
        # Save to Config
        import json
        config = {
            "mac_address": mac_address,
            "passkey": passkey,
            "calibration": {
                "slope": slope,
                "intercept": intercept
            }
        }
        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)
        print(f"\n[Saved] Calibration data saved to {config_file}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
```
