import asyncio
import logging
import json
import curses
import os
import sys
from bleak import BleakScanner
from pylifter.protocol import MoveCode, SmartPointCode
from pylifter.client import PyLifterClient, TESTED_FIRMWARE_VERSIONS, MoveCode, SmartPointCode

import argparse

# Configure logs
def configure_logging(enable_debug_file: bool = False):
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG) # Catch all, filter by handlers
    root_logger.handlers.clear()

    # File Handler (Optional)
    if enable_debug_file:
        fh = logging.FileHandler('debug.log', mode='w')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s:%(message)s'))
        root_logger.addHandler(fh)
        print("Debug logging enabled (debug.log)")

    # Console Handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING) # Suppress INFO logs (Connecting, etc) from library to console
    ch.setFormatter(logging.Formatter('%(message)s'))
    root_logger.addHandler(ch)

    logging.getLogger("pylifter").setLevel(logging.DEBUG)
    logging.getLogger("bleak").setLevel(logging.DEBUG)


async def check_firmware_support(version: str):
    """
    Checks if the firmware version is in the tested list. 
    If not, warns the user and prompts to continue or exit.
    """
    if version not in TESTED_FIRMWARE_VERSIONS:
        print("\n" + "="*60)
        print(f" WARNING: UNTESTED FIRMWARE VERSION DETECTED!")
        print(f" Current Version: {version}")
        print(f" Tested Versions: {', '.join(TESTED_FIRMWARE_VERSIONS)}")
        print("="*60)
        print(" Using this software with untested firmware may produce unpredictable results.")
        print(" Please use the official MyLifter app to update your winch firmware to a tested version.")
        print("="*60)
        
        while True:
            # Use run_in_executor to avoid blocking the asyncio loop (and keep-alives)
            print(" Press 'C' to CONTINUE anyway, or press ENTER to EXIT: ", end='', flush=True)
            choice = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            choice = choice.strip().upper()
            
            if choice == 'C':
                print(" Continuing with untested firmware... (Good luck!)")
                break
            elif choice == '':
                print(" Exiting.")
                sys.exit(0)
            else:
                pass # Invalid input, loop again

# ANSI Escape Codes
ANSI_UP = "\033[A"
ANSI_CLEAR = "\033[K"

class LiveStatusMonitor:
    def __init__(self, clients, target_ids):
        self.clients = clients
        self.target_ids = target_ids
        self.statuses = {cid: "Initializing..." for cid in target_ids}
        self.active = True
        self.lock = asyncio.Lock()
        
    def update_status(self, client_id, message):
        self.statuses[client_id] = message

    async def run(self):
        # Initial Print (Allocate lines)
        for cid in self.target_ids:
            print(f"  [{cid}] Waiting...")
            
        try:
            while self.active:
                # Move cursor up N lines
                print(f"{ANSI_UP * len(self.target_ids)}", end='', flush=True)
                
                for cid in self.target_ids:
                    client = self.clients.get(cid)
                    status_msg = self.statuses.get(cid, "Unknown")
                    
                    # Format:   [1] AA:BB:CC... | UP   | Pos: 123 | 45.0 cm | Status Msg
                    if client:
                        mac = client.mac_address
                        # Compact MAC (last 8 chars: "FE:15:33")
                        mac_short = mac[-8:] if mac else "??:??:??"
                        
                        pos = client._last_known_position
                        pos_str = str(pos) if pos is not None else "?"
                        dist = client.current_distance
                        
                        # Compact Connection Status
                        conn = "Conn" if client._is_connected else "Disc"
                        
                        # Compact Grid Format (<80 chars safe)
                        # [ 1] ..FE:15:33 | Conn | 12345 (123.4cm) | Status
                        # ID:4 | MAC:10 | S:3 | C:4 | S:3 | P:5 | S:2 | D:7 | S:3 | Msg
                        line = f"  [{cid:>2}] ..{mac_short} | {conn:<4} | {pos_str:>5} ({dist:>5.1f}cm) | {status_msg}"
                    else:
                        line = f"  [{cid:>2}] {'Unknown':<10} | {'':<4} | {'':<5} {'':<8} | {status_msg}"
                        
                    print(f"{line}{ANSI_CLEAR}")
                
                await asyncio.sleep(0.1)
        except Exception:
            pass # Handle exit gracefully

    def stop(self):
        self.active = False
        # Do one final print without moving up to leave state
        try:
             print(f"{ANSI_UP * len(self.target_ids)}", end='', flush=True)
             for cid in self.target_ids:
                client = self.clients.get(cid)
                status_msg = self.statuses.get(cid, "Done")
                if client:
                    mac = client.mac_address
                    mac_short = mac[-8:] if mac else "??:??:??"
                    pos = client._last_known_position
                    pos_str = str(pos) if pos is not None else "?"
                    conn = "Conn" if client._is_connected else "Disc"
                    
                    line = f"  [{cid:>2}] ..{mac_short} | {conn:<4} | {pos_str:>5} ({client.current_distance:>5.1f}cm) | {status_msg}"
                else:
                    line = f"  [{cid:>2}] Not Found"
                print(f"{line}{ANSI_CLEAR}")
        except:
            pass

async def monitor_move(client: PyLifterClient, target_pos: int, direction: MoveCode, client_id: int, speed: int = 100, monitor: LiveStatusMonitor = None):
    """
    Moves the winch in 'direction' until 'target_pos' is reached.
    Updates 'monitor' with status strings instead of printing.
    """
    def log(msg):
        if monitor: monitor.update_status(client_id, msg)
        else: print(f"[Winch {client_id}] {msg}")

    # Safety Check
    start_pos = client._last_known_position
    if start_pos is None:
        log("Error: Unknown start position.")
        return

    log(f"Moving {'UP' if direction == MoveCode.UP else 'DOWN'} -> {target_pos} ({speed}%)")
    
    if not client._is_connected:
        log("Not Connected! Aborting.")
        return

    await client.clear_error()
    await asyncio.sleep(0.25)
    
    await client.move(direction, speed=speed)
    
    try:
        while True:
            # Robust Connection Check
            if not client._is_connected or (client._client and not client._client.is_connected):
                log("Connection Lost!")
                break

            current_pos = client._last_known_position
            
            # Check Limits
            if direction == MoveCode.UP:
                if current_pos >= target_pos: 
                    log("Target Reached.")
                    break
                if client.last_error_code == 0x86:
                     log("Reached Top Limit (Hardware).")
                     break
                if client.last_error_code == 0x81:
                     log("Reached Top Soft Limit.")
                     await client.stop()
                     break

            else:
                if current_pos <= target_pos: 
                    log("Target Reached.")
                    break
                if client.last_error_code == 0x86:
                     log("Reached Bottom Limit (Hardware).")
                     break
                if client.last_error_code == 0x81:
                     log("Reached Bottom Soft Limit.")
                     await client.stop()
                     break
            
            # Update running status
            log(f"Moving {'UP' if direction == MoveCode.UP else 'DOWN'}... ({speed}%)")
            await asyncio.sleep(0.1)
            
    finally:
        await client.stop()
        await asyncio.sleep(0.5) 
        log("Stopped.")

async def monitor_smart_move(client: PyLifterClient, direction: MoveCode, client_id: int, monitor: LiveStatusMonitor = None):
    """
    Monitors a 'Smart Move' (LIFT/LOWER).
    """
    def log(msg):
        if monitor: monitor.update_status(client_id, msg)
        else: print(f"[Winch {client_id}] {msg}")

    limit_name = "TOP" if direction == MoveCode.SMART_UP else "BOTTOM"
    log(f"Smart Moving {limit_name}...")
    
    await client.clear_error()
    await asyncio.sleep(0.25)
    
    await client.move(direction, speed=100)
    
    last_pos = client._last_known_position
    stable_count = 0
    
    try:
        while True:
            current_pos = client._last_known_position
            
            if client.last_error_code == 0x86:
                 log("Reached Hardware Limit.")
                 break
            if client.last_error_code == 0x83:
                 log(f"Failed: {limit_name} Limit Not Set.")
                 break
            if client.last_error_code == 0x81:
                 log(f"Reached {limit_name} Soft Limit.")
                 break
            
            # Stall Detection (Smart Move implies auto-stop, but we check specific conditions)
            if current_pos == last_pos:
                stable_count += 1
                if stable_count > 20: 
                    log("Movement Stopped (Stable).")
                    break
            else:
                stable_count = 0
                last_pos = current_pos
            
            log(f"Smart Moving {limit_name}...")
            await asyncio.sleep(0.1)
            
    finally:
        await client.stop()
        await asyncio.sleep(0.5)
        log("Stopped.")

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
        
        # STABILITY FIX: Wait for BLE stack to settle
        await asyncio.sleep(1.5)
        
        try:
            ver = await new_client.get_version()
            proto_ver = await new_client.get_protocol_version()
            print(f"Firmware Version: {ver}")
            print(f"Protocol Version: {proto_ver}")
            
            await check_firmware_support(ver)
        except Exception as ve:
            print(f"[Warning] Could not verify firmware version: {ve}")
        
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
    # Disconnect if active
    if target_id in clients:
        if clients[target_id]._is_connected:
            print(f"Disconnecting ID {target_id}...", end='', flush=True)
            await clients[target_id].disconnect()
            # Wait for stack to settle invisibly
            await asyncio.sleep(2.0)
            print(" Disconnected.")
        else:
            print(f"Skipping disconnect for ID {target_id} (Already Disconnected).")
    
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
        # Give adapter time to settle if we just started
        await asyncio.sleep(2.0)
        for cid, client in clients.items():
            print(f"[{cid}] Connecting to {client.mac_address}...", end='', flush=True)
            connected = False
            for attempt in range(3):
                try:
                    await client.connect()
                    print(" Connected.", end='', flush=True)
                    connected = True
                    
                    # STABILITY FIX: Wait for BLE stack to settle before querying versions
                    # High traffic immediately after connection can cause Service Discovery errors
                    await asyncio.sleep(1.5)

                    try:
                        # Print Version
                        ver = await client.get_version()
                        proto_ver = await client.get_protocol_version()
                        print(f" (Versions: Firmware={ver} | Protocol={proto_ver})")
                        
                        await check_firmware_support(ver)
                    except Exception as ve:
                        print(f"\n      [Warning] Could not verify firmware version: {ve}")
                    
                    break # Success, exit retry loop
                    
                except Exception as e:
                    if attempt < 2:
                        print(f" Failed (Retry {attempt+1}/3): {e} ...", end='', flush=True)
                        await asyncio.sleep(2.0)
                    else:
                        print(f" Failed: {e}")
            
            if not connected:
                print(f"      [Error] Could not connect to winch {cid} after 3 attempts.")
        
    def print_status():
        print("\nStatus:")
        if not clients:
            print("  (No winches configured)")
        for cid, c in clients.items():
            status = "Connected" if c._is_connected else "Disconnected"
            print(f"  [{cid}] {c.mac_address}: {status} | Pos={c._last_known_position} | {c.current_distance:.1f} cm")
            
    print("\n--- Ready ---")
    
    skip_status = False
    while True:
        # Print Status
        if not skip_status:
            print_status()
        skip_status = False # Reset for next loop

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
        
        # Normalize Synonyms
        if cmd == "UP": cmd = "U"
        if cmd == "DOWN": cmd = "D"
        
        if cmd == 'Q':
            break
            
        if cmd == 'P':
            # Just continue to loop start which prints status
            continue
            
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
            print("  U <val> [spd] : Move UP by <val> cm, optional speed 25-100% (Synonym: UP)")
            print("  D <val> [spd] : Move DOWN by <val> cm, optional speed 25-100% (Synonym: DOWN)")
            print("  LIFT          : Smart Lift (Move UP to High Limit)")
            print("  LOWER         : Smart Lower (Move DOWN to Low Limit)")
            print("  SH            : Set HIGH (Top) Soft Limit at current position")
            print("  SL            : Set LOW (Bottom) Soft Limit at current position")
            print("  CH            : Clear HIGH (Top) Soft Limit")
            print("  CL            : Clear LOW (Bottom) Soft Limit")
            print("  PAIR          : Scan and Pair a NEW winch")
            print("  UNPAIR        : Remove a winch from configuration")
            print("  P             : Print Status of all winches")
            print("  Q             : Quit")
            print("\nExamples:")
            print("  1 U 10        -> Move Winch 1 UP by 10cm at 100% speed")
            print("  1 U 10 50     -> Move Winch 1 UP by 10cm at 50% speed")
            print("  ALL LIFT      -> Smart Lift ALL Winches")
            print("  1,2 LIFT    -> Smart Lift Winches 1 and 2")
            print("  PAIR        -> Start Pairing Mode")
            print("--------------------\n")

        if cmd == '?':
            print_help()
            continue

        # Validate Command
        valid_cmds = ['LIFT', 'LOWER', 'SH', 'SL', 'CH', 'CL', 'CB', 'U', 'D']
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
        
        # Setup Monitor for Movement Commands
        monitor = None
        if cmd in ['LIFT', 'LOWER', 'U', 'D']:
            monitor = LiveStatusMonitor(clients, target_ids)
            tasks.append(monitor.run())
        
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
                tasks.append(monitor_smart_move(client, MoveCode.SMART_UP, tid, monitor))
            elif cmd == 'LOWER':
                tasks.append(monitor_smart_move(client, MoveCode.SMART_DOWN, tid, monitor))
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
            elif cmd == 'CH':
                 async def do_ch(c, i):
                     print(f"[Winch {i}] Clearing High Limit...")
                     await c.clear_smart_point(SmartPointCode.TOP)
                 tasks.append(do_ch(client, tid))
            elif cmd in ['CL', 'CB']: # Support CL or CB
                 async def do_cl(c, i):
                     print(f"[Winch {i}] Clearing Low Limit...")
                     await c.clear_smart_point(SmartPointCode.BOTTOM)
                 tasks.append(do_cl(client, tid))
            elif cmd in ['U', 'D']:
                try:
                    delta_cm = float(args[0])
                    
                    # Parse Speed (Optional)
                    speed = 100
                    if len(args) > 1:
                        try:
                            speed = int(args[1])
                            if not (25 <= speed <= 100):
                                print(f"Error: Speed must be 25-100.")
                                print_help()
                                continue
                        except ValueError:
                            print(f"Error: Invalid speed '{args[1]}'. Using 100.")
                            print_help()
                            continue

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
                    
                    # Note: We disabled interactive override for batch moves with monitor
                    # The monitor doesn't support input() easily regardless of batch size
                    tasks.append(monitor_move(client, target_pos, direction, tid, speed=speed, monitor=monitor))
                except ValueError:
                    print(f"Invalid Value: {args[0]}")
            else:
                pass
                
        if tasks:
            if monitor:
                # Run everything, monitor is one of the tasks
                # But we need to ensure monitor.stop() is called when move tasks finish
                # gather() will run monitor forever (it loops while active) which will block?
                # No, monitor.run() loops while self.active.
                # So we need to await the MOVEMENT tasks, then stop monitor.
                
                # Separate monitor task from work tasks
                monitor_task = tasks.pop(0) # The first one we added
                worker_tasks = tasks
                
                # Start Monitor
                m_task = asyncio.create_task(monitor_task)
                
                # Run Workers
                await asyncio.gather(*worker_tasks)
                
                # Stop Monitor
                monitor.stop()
                await m_task
                
                # Prevent re-printing status since monitor just showed the final state
                skip_status = True
            else:
                await asyncio.gather(*tasks)

    print("Disconnecting all...")
    
    # Use Monitor for Disconnects
    all_ids = list(clients.keys())
    if all_ids:
        disc_monitor = LiveStatusMonitor(clients, all_ids)
        # We run the monitor in background while we process disconnects
        mon_task = asyncio.create_task(disc_monitor.run())
        
        for cid in all_ids:
            c = clients[cid]
            if c._is_connected:
                disc_monitor.update_status(cid, "Disconnecting...")
                await c.disconnect()
                disc_monitor.update_status(cid, "Disconnected")
            else:
                 disc_monitor.update_status(cid, "Already Disconnected")
                 
        disc_monitor.stop()
        await mon_task

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyLifter Interactive Demo")
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging to debug.log")
    args = parser.parse_args()
    
    configure_logging(enable_debug_file=args.debug)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass # Clean exit on Ctrl+C
