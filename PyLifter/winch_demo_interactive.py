
import asyncio
import logging
import json
import curses
import os
from bleak import BleakScanner
from pylifter import PyLifterClient, MoveCode, SmartPointCode

# Configure logs to be minimal
logging.basicConfig(level=logging.WARNING)

async def monitor_move(client: PyLifterClient, target_pos: int, direction: MoveCode, client_id: int):
    """
    Moves the winch in 'direction' until 'target_pos' is reached.
    """
    prefix = f"[Winch {client_id}]"
    
    # Safety Check
    start_pos = client._last_known_position
    if start_pos is None:
        print(f"{prefix} Error: Unknown start position.")
        return

    print(f"{prefix} Moving {'UP' if direction == MoveCode.UP else 'DOWN'} to Target Pos: {target_pos}")
    
    # CRITICAL Fix: Clear any existing End-of-Travel errors before moving
    await client.clear_error()
    await asyncio.sleep(0.25)
    
    await client.move(direction, speed=100)
    
    is_overridden = False
    
    try:
        while True:
            current_pos = client._last_known_position
            dist = client.current_distance
            
            # TODO: How to print status for multiple winches?
            # For now, just print updates on new lines or use a simpler log.
            # print(f"     Pos: {current_pos:<6} | Dist: {dist:.1f} cm", end='\r') 
            
            # Check Condition
            if direction == MoveCode.UP:
                if current_pos >= target_pos: break
                if client.last_error_code == 0x86:
                     print(f"{prefix} Reached Top Limit (0x86).")
                     break
                if client.last_error_code == 0x81 and not is_overridden:
                     print(f"{prefix} Reached Soft Limit (0x81).")
                     await client.stop()
                     # Override Logic is tricky for multi-winch. For now, just stop.
                     print(f"{prefix} Soft Limit Hit. Override not implemented for multi-winch batch move.")
                     break
            else:
                if current_pos <= target_pos: break
                if client.last_error_code == 0x86:
                     print(f"{prefix} Reached Bottom Limit (0x86).")
                     break
                if client.last_error_code == 0x81 and not is_overridden:
                     print(f"{prefix} Reached Soft Limit (0x81).")
                     await client.stop()
                     print(f"{prefix} Soft Limit Hit.")
                     break
            
            await asyncio.sleep(0.1)
            
    finally:
        await client.stop()
        await asyncio.sleep(0.5) 
        print(f"{prefix} Stopped at: {client._last_known_position} | {client.current_distance:.1f} cm")

async def monitor_smart_move(client: PyLifterClient, direction: MoveCode, client_id: int):
    """
    Monitors a 'Smart Move' (LIFT/LOWER).
    """
    prefix = f"[Winch {client_id}]"
    print(f"{prefix} Smart Moving {'UP' if direction == MoveCode.SMART_UP else 'DOWN'}...")
    
    await client.clear_error()
    await asyncio.sleep(0.25)
    
    await client.move(direction, speed=100)
    
    last_pos = client._last_known_position
    stable_count = 0
    
    try:
        while True:
            current_pos = client._last_known_position
            
            if client.last_error_code == 0x86:
                 print(f"{prefix} Reached Limit (0x86).")
                 break
            if client.last_error_code == 0x81:
                 print(f"{prefix} Reached Soft Limit (0x81).")
                 break
            
            if current_pos == last_pos:
                stable_count += 1
                if stable_count > 20: 
                    print(f"{prefix} Movement Stopped.")
                    break
            else:
                stable_count = 0
                last_pos = current_pos
            
            await asyncio.sleep(0.1)
            
    finally:
        await client.stop()
        await asyncio.sleep(0.5)
        print(f"{prefix} Stopped at: {client._last_known_position} | {client.current_distance:.1f} cm")

async def pair_new_winch(config_file, config, clients):
    print("\n--- Pairing Mode ---")
    print("Scanning for devices...")
    devices = await BleakScanner.discover()
    
    # Filter or just show all
    mylifters = [d for d in devices if d.name and ("mylifter" in d.name.lower() or "levitation" in d.name.lower())] # Case insensitive
    
    if not mylifters:
        print("No MyLifter devices found (Check filters or scan again).")
        # Fallback to show all named devices
        mylifters = [d for d in devices if d.name]

    for i, d in enumerate(mylifters):
        print(f"{i+1}. {d.name} ({d.address})")

    sel = await asyncio.get_event_loop().run_in_executor(None, input, "Select device # (or 0 to cancel): ")
    try:
        idx = int(sel) - 1
        if idx < 0 or idx >= len(mylifters):
            print("Cancelled.")
            return
    except:
        return

    device = mylifters[idx]
    
    # Determine next ID
    existing_ids = [d['id'] for d in config.get("devices", [])]
    next_id = 1
    if existing_ids:
        next_id = max(existing_ids) + 1
        
    print(f"Pairing {device.name} as ID {next_id}...")
    print("!!! PRESS THE BUTTON ON THE WINCH NOW !!!")
    
    client = PyLifterClient(device.address)
    try:
        # Don't wait for position sync during pairing (avoids warning)
        await client.connect(wait_for_position=False)
        
        # Verify Passkey was received
        if not client.passkey:
            print("[ERROR] Pairing Failed: No passkey received from device.")
            print("        Make sure you pressed the button on the winch when prompted.")
            await client.disconnect()
            return

        print("Paired successfully.")
        
        # Add to config
        new_device_entry = {
            "id": next_id,
            "mac_address": device.address,
            "passkey": client.passkey.hex() if client.passkey else None
        }
        
        if "devices" not in config: config["devices"] = []
        config["devices"].append(new_device_entry)
        
        # Save
        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)
            
        print("Saved to config.")
        
        # Setup Calibration (Global)
        calibration = config.get("calibration", {})
        slope = calibration.get("slope", 1.0)
        intercept = calibration.get("intercept", 0.0)
        
        # Disconnect pairing client to ensure clean state
        await client.disconnect()
        await asyncio.sleep(1.0)
        
        # Reconnect with fresh client (mimic startup)
        print("Connecting to new winch...")
        new_client = PyLifterClient(device.address, passkey=client.passkey.hex())
        new_client.set_unit_calibration(slope, intercept)
        await new_client.connect()
        print("Connected.")
        
        # Add to active clients
        clients[next_id] = new_client
        
    except Exception as e:
        print(f"Pairing failed/aborted: {e}")
        if client._is_connected:
             await client.disconnect()

async def unpair_winch(config_file, config, clients):
    print("\n--- Unpair Mode ---")
    devices = config.get("devices", [])
    if not devices:
        print("No devices configured.")
        return

    for dev in devices:
        did = dev['id']
        mac = dev['mac_address']
        status = "Connected" if did in clients and clients[did]._is_connected else "Disconnected"
        print(f"ID {did}: {mac} ({status})")

    sel = await asyncio.get_event_loop().run_in_executor(None, input, "Select ID to UNPAIR (or 0 to cancel): ")
    try:
        target_id = int(sel)
        if target_id == 0: return
    except:
        return

    # Find device in list
    dev_entry = next((d for d in devices if d['id'] == target_id), None)
    if not dev_entry:
        print("Invalid ID.")
        return

    print(f"Unpairing ID {target_id}...")
    
    # Disconnect if active
    if target_id in clients:
        print(f"Disconnecting ID {target_id}...", end='', flush=True)
        await clients[target_id].disconnect()
        # Wait for stack to settle invisibly
        await asyncio.sleep(3.0)
        print(" Disconnected.")
    
    # Remove from devices list
    remaining_devices = [d for d in devices if d['id'] != target_id]
    
    # Renumbering Logic
    new_clients_map = {}
    config["devices"] = []
    
    print("\nRenumbering remaining winches...")
    
    # Sort by old ID to maintain relative order
    remaining_devices.sort(key=lambda x: x['id'])
    
    for i, dev in enumerate(remaining_devices):
        old_id = dev['id']
        new_id = i + 1
        
        dev['id'] = new_id
        config["devices"].append(dev)
        
        # Move client instance to new ID key
        if old_id in clients:
            new_clients_map[new_id] = clients[old_id]
            
        if old_id != new_id:
            print(f"  ID {old_id} -> ID {new_id}")
            
    # Update active clients map
    clients.clear()
    clients.update(new_clients_map)
    
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)
        
    print("Unpaired and configuration updated.")

async def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "pylifter_config.json")
    
    config = {}
    
    # 1. Load Config
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("[Config] No config file found.")
        config = {"calibration": {}, "devices": []}

    # 2. Setup Clients
    clients = {} # ID -> Client
    
    calibration = config.get("calibration", {})
    slope = calibration.get("slope", 1.0)
    intercept = calibration.get("intercept", 0.0)
    
    # Check for legacy config format (migration fallback)
    if "mac_address" in config and "devices" not in config:
        print("Legacy config detected in memory (file was migrated?). Using defaults.")
    
    device_list = config.get("devices", [])
    if not device_list and "mac_address" in config:
         # Handle legacy migration in case file wasn't written correctly? 
         # I overwrote it in previous step relative to this run, so it should be fine.
         pass

    print(f"\n--- Initializing {len(device_list)} Winches ---")
    
    for dev in device_list:
        dev_id = dev["id"]
        mac = dev["mac_address"]
        pk = dev.get("passkey")
        
        client = PyLifterClient(mac, passkey=pk)
        client.set_unit_calibration(slope, intercept)
        clients[dev_id] = client

    # 3. Connect All
    if clients:
        print("Connecting to all winches...")
        for cid, client in clients.items():
            print(f"[{cid}] Connecting to {client.mac_address}...", end='', flush=True)
            try:
                await client.connect()
                print(" Connected.")
            except Exception as e:
                print(f" Failed: {e}")
        
    print("\n--- Ready ---")
    
    while True:
        # Print Status
        print("\nStatus:")
        if not clients:
            print("  (No winches configured)")
        for cid, c in clients.items():
            status = "Connected" if c._is_connected else "Disconnected"
            print(f"  [{cid}] {c.mac_address}: {status} | Pos={c._last_known_position} | {c.current_distance:.1f} cm")

        cmd_str = await asyncio.get_event_loop().run_in_executor(None, input, "\nCommand ([ID,ID | all] <CMD>... | PAIR | Q | ?): ")
        
        parts = cmd_str.strip().split()
        if not parts: continue
        
        # Parse Targets
        # Check if first part is ID list (digits or commas)
        # e.g. "1,2", "1", "1, 3" is harder to split.
        # Logic: If first part starts with digit, assume it's IDs.
        
        target_ids = []
        cmd_start_idx = 0
        
        potential_ids = parts[0]
        
        if potential_ids.upper() == "ALL":
             # Target ALL configured winches
             target_ids = list(clients.keys())
             cmd_start_idx = 1
        elif potential_ids[0].isdigit():
            # Parse IDs
            try:
                id_strs = potential_ids.split(',')
                for s in id_strs:
                    if s.strip():
                        target_ids.append(int(s.strip()))
                cmd_start_idx = 1
            except ValueError:
                # Not IDs, maybe command starts with digit? Unlikely.
                pass
        
        if not target_ids:
            target_ids = [1] # Default to 1
        
        # Get actual command part
        if cmd_start_idx >= len(parts):
            continue
            
        cmd = parts[cmd_start_idx].upper()
        args = parts[cmd_start_idx+1:]
        
        if cmd == 'Q':
            break
            
        if cmd == 'PAIR':
            await pair_new_winch(config_file, config, clients)
            continue
            
        if cmd == 'UNPAIR':
            await unpair_winch(config_file, config, clients)
            continue
            
        def print_help():
            print("\n--- Command Help ---")
            print("Syntax: [ID,ID...|ALL] <COMMAND> [ARGS]")
            print("  (If no ID is specified, Command applies to ID 1)")
            print("\nAvailable Commands:")
            print("  U <val>     : Move UP by <val> centimeters")
            print("  D <val>     : Move DOWN by <val> centimeters")
            print("  LIFT        : Smart Lift (Move UP to High Limit)")
            print("  LOWER       : Smart Lower (Move DOWN to Low Limit)")
            print("  SH          : Set HIGH (Top) Soft Limit at current position")
            print("  SL          : Set LOW (Bottom) Soft Limit at current position")
            print("  PAIR        : Scan and Pair a NEW winch")
            print("  UNPAIR      : Remove a winch from configuration")
            print("  Q           : Quit")
            print("\nExamples:")
            print("  1 U 10      -> Move Winch 1 UP by 10cm")
            print("  ALL LIFT    -> Smart Lift ALL Winches")
            print("  1,2 LIFT    -> Smart Lift Winches 1 and 2")
            print("  PAIR        -> Start Pairing Mode")
            print("--------------------\n")

        if cmd == '?':
            print_help()
            continue

        # Validate Command
        valid_cmds = ['LIFT', 'LOWER', 'SH', 'SL', 'U', 'D']
        if cmd not in valid_cmds:
            print(f"\n[ERROR] Unknown command: '{cmd}'")
            print_help()
            continue
            
        # Validate Args
        if cmd in ['U', 'D'] and not args:
             print(f"\n[ERROR] Command '{cmd}' requires a distance argument.")
             print_help()
             continue

        # Execute Command on Targets
        tasks = []
        
        for tid in target_ids:
            if tid not in clients:
                print(f"Warning: Winch {tid} not configured.")
                continue
                
            client = clients[tid]
            if not client._is_connected:
                print(f"Warning: Winch {tid} not connected (Skipping).")
                continue
            
            # Dispatch Command
            if cmd == 'LIFT':
                tasks.append(monitor_smart_move(client, MoveCode.SMART_UP, tid))
            elif cmd == 'LOWER':
                tasks.append(monitor_smart_move(client, MoveCode.SMART_DOWN, tid))
            elif cmd == 'SH':
                 async def do_sh(c, i):
                     print(f"[Winch {i}] Setting High Limit...")
                     await c.set_smart_point(SmartPointCode.TOP)
                 tasks.append(do_sh(client, tid))
            elif cmd == 'SL':
                 async def do_sl(c, i):
                     print(f"[Winch {i}] Setting Low Limit...")
                     await c.set_smart_point(SmartPointCode.BOTTOM)
                 tasks.append(do_sl(client, tid))
            elif cmd in ['U', 'D']:
                try:
                    delta_cm = float(args[0])
                    current_dist = client.current_distance
                    
                    if cmd == 'U':
                        target_dist = current_dist - delta_cm
                        direction = MoveCode.UP
                    else:
                        target_dist = current_dist + delta_cm
                        direction = MoveCode.DOWN
                        
                    if slope == 0:
                        print(f"[Winch {tid}] Cal slope is 0!")
                        continue
                        
                    target_pos = int((target_dist - intercept) / slope)
                    tasks.append(monitor_move(client, target_pos, direction, tid))
                except ValueError:
                    print(f"Invalid Value: {args[0]}")
            else:
                pass
                
        if tasks:
            await asyncio.gather(*tasks)

    print("Disconnecting all...")
    for c in clients.values():
        await c.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
