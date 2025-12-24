
import asyncio
import logging
from pylifter import PyLifterClient, MoveCode

# Configure logging to show only critical errors from the library, 
# so our print statements are clean.
logging.basicConfig(level=logging.WARNING)

async def monitor_position(client: PyLifterClient, duration: float):
    """Monitors and prints the winch position for a set duration."""
    end_time = asyncio.get_event_loop().time() + duration
    while asyncio.get_event_loop().time() < end_time:
        pos = client._last_known_position
        dist = client.current_distance
        print(f"  -> Pos: {pos:<6} | Dist: {dist:.1f} cm")
        await asyncio.sleep(0.5)

async def main():
    import json
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
    if "slope" in calibration and "intercept" in calibration:
        client.set_unit_calibration(calibration["slope"], calibration["intercept"])
    
    try:
        print("\n--- PyLifter Winch Demo ---\n")
        
        # 2. Connect
        print("1. Connecting to Winch...")
        await client.connect()
        print("   Connected and Synced.")
        
        # Save passkey if newly acquired or config missing
        if client.passkey and client.passkey != passkey:
             config["passkey"] = client.passkey.hex() if isinstance(client.passkey, bytes) else client.passkey
             with open(config_file, "w") as f:
                 json.dump(config, f, indent=4)
             print(f"   [Persistence] Updated passkey in {config_file}")
        
        # 3. Move UP
        print("\n2. Moving UP (3 seconds)...")
        await client.move(MoveCode.UP, speed=100)
        await monitor_position(client, 3.0)
        
        # 4. Stop
        print("\n3. Stopping...")
        await client.stop()
        await asyncio.sleep(1.0)
        print(f"   Stopped at Pos: {client._last_known_position} | Dist: {client.current_distance:.1f} cm")
        
        # 5. Move DOWN
        print("\n4. Moving DOWN (3 seconds)...")
        await client.move(MoveCode.DOWN, speed=100)
        await monitor_position(client, 3.0)
        
        # 6. Stop
        print("\n5. Stopping...")
        await client.stop()
        await asyncio.sleep(1.0)
        print(f"   Stopped at Pos: {client._last_known_position} | Dist: {client.current_distance:.1f} cm")
        
    except Exception as e:
        print(f"\n[ERROR] Demo Failed: {e}")
    finally:
        print("\n6. Disconnecting...")
        await client.disconnect()
        print("   Done.")

if __name__ == "__main__":
    asyncio.run(main())
