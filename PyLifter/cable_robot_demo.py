import asyncio
import json
import logging
import math
import os
import sys
import argparse
from pylifter.client import PyLifterClient, MoveCode

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("cable_robot")

# ==========================================
# Simulated Client for Offline Testing
# ==========================================
class SimulatedLifterClient:
    def __init__(self, mac_address, passkey=None):
        self.mac_address = mac_address
        self.passkey = passkey
        self._is_connected = False
        self._last_known_position = 0
        self._cal_slope = 1.0
        self._cal_intercept = 0.0
        self.current_distance = 0.0
        self.last_error_code = 0 # Match PyLifterClient
        
    def set_unit_calibration(self, slope, intercept):
        self._cal_slope = slope
        self._cal_intercept = intercept
        
    async def connect(self):
        print(f"[SIM] Connecting to {self.mac_address}...")
        await asyncio.sleep(0.5)
        self._is_connected = True
        print(f"[SIM] Connected to {self.mac_address}.")
        
    async def disconnect(self):
        self._is_connected = False
        print(f"[SIM] Disconnected {self.mac_address}.")

    async def move(self, direction, speed=100):
        if not self._is_connected: return
        # In sim, we don't naturally move over time unless we simulate a loop.
        # But our monitor loop expects _last_known_position to change.
        # We'll just define a target and let a background task update it?
        # Or simpler: The monitor loop in CableRobot waits for current_pos to reach target.
        # We can implement a "move_to_instant" helper or just update position immediately for this demo?
        # To test coordination, immediate update is boring.
        # Let's simulate movement in a background task.
        pass

    async def stop(self):
        pass

    async def set_smart_point(self, point):
        print(f"[SIM] Set Smart Point {point}")
        
    async def clear_smart_point(self, point):
        print(f"[SIM] Clear Smart Point {point}")
        
    async def get_stats(self):
        return b''

    # Helper for Simulation only
    async def sim_update_pos(self, target_pos, speed):
        # Move from current to target over time
        start = self._last_known_position
        dist_ticks = target_pos - start
        steps = 20
        for i in range(steps):
             if not self._is_connected: break
             # Linear interp
             self._last_known_position = int(start + (dist_ticks * (i+1)/steps))
             
             # Reverse calc distance for display
             self.current_distance = (self._cal_slope * self._last_known_position) + self._cal_intercept
             await asyncio.sleep(0.05)


class CableRobot:
    def __init__(self, config, sim_mode=False):
        self.sim_mode = sim_mode
        self.dims = config.get("dimensions", {})
        self.width = self.dims.get("width_cm", 400.0)
        self.length = self.dims.get("length_cm", 400.0)
        self.height = self.dims.get("height_cm", 300.0)
        
        self.safety = config.get("safety", {})
        self.min_floor_margin = self.safety.get("min_floor_margin_cm", 20.0)
        self.min_ceiling_margin = self.safety.get("min_ceiling_margin_cm", 50.0)
        self.safe_angle_deg = self.safety.get("safe_angle_deg", 60.0)
        
        # Anchor Points (Pulleys) at Height H
        # Order: 1:FL, 2:FR, 3:BR, 4:BL
        # Coordinate System: Origin @ Floor Front-Left.
        # FL=(0,0,H), FR=(W,0,H), BR=(W,L,H), BL=(0,L,H)
        h = self.height
        w = self.width
        l = self.length
        
        self.anchors = {
            1: (0.0, 0.0, h),   # Front-Left
            2: (w,   0.0, h),   # Front-Right
            3: (w,   l,   h),   # Back-Right
            4: (0.0, l,   h)    # Back-Left
        }
        
        self.clients = {} # ID -> Client

    def inverse_kinematics(self, x, y, z):
        """
        Calculate required cable lengths for a given point (x, y, z).
        Returns: dict {winch_id: length_cm}
        """
        lengths = {}
        for wid, anchor in self.anchors.items():
            ax, ay, az = anchor
            dist = math.sqrt((x - ax)**2 + (y - ay)**2 + (z - az)**2)
            lengths[wid] = dist
        return lengths

    def is_safe(self, x, y, z):
        """
        Check if point is within the "Inverted Pyramid" safety zone.
        """
        # 1. Basic Box Limits
        if not (0 <= x <= self.width): return False, "X out of bounds"
        if not (0 <= y <= self.length): return False, "Y out of bounds"
        if not (self.min_floor_margin <= z <= (self.height - self.min_ceiling_margin)):
            return False, "Z out of bounds (Floor/Ceiling Margin)"

        # 2. Max Angle / Inverted Pyramid Constraint
        # We ensure that for every cable, the angle with vertical (Z) is within safe limit.
        # This naturally forms an inverted pyramid shape.
        # Angle theta = atan(horizontal_dist / vertical_dist)
        # vertical_dist = H - z
        
        h_dist = self.height - z
        if h_dist <= 0.1: return False, "Too close to ceiling (Singularity)"

        max_tan = math.tan(math.radians(self.safe_angle_deg))
        
        for wid, anchor in self.anchors.items():
            ax, ay, az = anchor
            # Horizontal distance to anchor projected on XY plane
            horiz_dist = math.sqrt((x - ax)**2 + (y - ay)**2)
            
            tan_theta = horiz_dist / h_dist
            if tan_theta > max_tan:
                return False, f"Cable {wid} angle too steep ({math.degrees(math.atan(tan_theta)):.1f}° > {self.safe_angle_deg}°)"

        return True, "Safe"

    async def initialize_winches(self, winch_config):
        configured_devices = winch_config.get("devices", [])
        
        print(f"Initializing {len(configured_devices)} Winches for Cable Robot (Sim={self.sim_mode})...")
        
        for dev in configured_devices:
            did = dev['id']
            if did not in self.anchors:
                print(f"Warning: Device ID {did} not mapped to an anchor. Ignoring.")
                continue
            
            if self.sim_mode:
                client = SimulatedLifterClient(dev['mac_address'], passkey=dev.get('passkey'))
            else:
                client = PyLifterClient(dev['mac_address'], passkey=dev.get('passkey'))
            
            # Apply Calibration
            slope = winch_config.get("calibration", {}).get("slope", 1.0)
            intercept = winch_config.get("calibration", {}).get("intercept", 0.0)
            client.set_unit_calibration(slope, intercept)
            
            self.clients[did] = client
            
        # Connect sequentially to avoid "Operation in progress" BlueZ errors
        print("Connecting to winches sequentially...")
        for did, client in self.clients.items():
            print(f"  [{did}] Connecting to {client.mac_address}...")
            try:
                await client.connect()
                print(f"  [{did}] Connected.")
            except Exception as e:
                print(f"  [{did}] Connection Failed: {e}")
                
        print("Winch initialization complete.")

    async def move_to(self, x, y, z, speed=50, override_sl=None):
        # override_sl: List of WIDs to force-move (bypass soft limits), or True for all
        
        # 1. Check Safety
        safe, msg = self.is_safe(x, y, z)
        if not safe:
            print(f"[SAFETY ERROR] Move Rejected: {msg}")
            return False, {}

        # 2. Check Connection Status
        for wid, client in self.clients.items():
            if not client._is_connected:
                print(f"[CONNECTION ERROR] Move Rejected: Winch {wid} is disconnected.")
                return False, {}

        # 3. Calculate Target Lengths
        targets = self.inverse_kinematics(x, y, z)
        
        # 4. Calculate Speeds (Synchronized)
        # Find max delta to scale speeds
        max_delta = 0.0
        deltas = {}
        
        for wid, target_len in targets.items():
            if wid in self.clients:
                curr = self.clients[wid].current_distance
                delta = abs(target_len - curr)
                deltas[wid] = delta
                if delta > max_delta:
                    max_delta = delta

        print(f"Moving to ({x:.1f}, {y:.1f}, {z:.1f})... MaxDelta={max_delta:.1f}cm")
        
        # Shared abort event for emergency stop
        abort_event = asyncio.Event()
        
        tasks = []
        active_wids = []
        for wid, length in targets.items():
            if wid in self.clients:
                client = self.clients[wid]
                if client._cal_slope == 0: continue
                
                target_pos = int((length - client._cal_intercept) / client._cal_slope)
                current_pos = client._last_known_position if client._last_known_position is not None else 0
                
                current_len = client.current_distance
                
                # Determine Direction (Normal or Override)
                use_override = False
                if override_sl is True:
                    use_override = True
                elif isinstance(override_sl, list) and wid in override_sl:
                    use_override = True
                    
                if length < current_len:
                    direction = MoveCode.OVERRIDE_UP if use_override else MoveCode.UP
                    dir_base = "UP (Retract)"
                else:
                    direction = MoveCode.OVERRIDE_DOWN if use_override else MoveCode.DOWN
                    dir_base = "DOWN (Extend)"
                    
                dir_str = f"{dir_base} [OVERRIDE]" if use_override else dir_base
                
                # Calculate Synchronized Speed
                delta = deltas.get(wid, 0.0)
                if max_delta > 0.5: # Avoid division by zero
                    calc_speed = speed * (delta / max_delta)
                else:
                    calc_speed = float(speed)
                
                # Apply constraints: Min 25, Max 100 (or user Max)
                final_speed = int(max(calc_speed, 25))
                final_speed = min(final_speed, 100)
                
                print(f"  [CMD] Winch {wid}: Length {current_len:.1f}cm -> {length:.1f}cm (Pos {client._last_known_position}->{target_pos}) | {dir_str} | Speed={final_speed}")
                
                tasks.append(self._monitor_single_move(client, wid, target_pos, direction, final_speed, abort_event))
                active_wids.append((wid, direction))

        results = await asyncio.gather(*tasks)
        
        # Check results
        soft_limit_hit_info = {} # {wid: direction}
        hard_limit_wids = []
        is_hard_limit = False

        for i, (success, msg) in enumerate(results):
            if not success and msg == "SOFT_LIMIT":
                wid, direction = active_wids[i]
                soft_limit_hit_info[wid] = direction
            if not success and msg == "HARD_LIMIT":
                wid, _ = active_wids[i]
                hard_limit_wids.append(wid)
                is_hard_limit = True
        
        if abort_event.is_set():
            if is_hard_limit:
                print(f"[ERROR] Movement stopped due to Hard Limit (End of Travel) on Winch(es): {hard_limit_wids}")
            else:
                print("[EMERGENCY STOP] Movement aborted due to winch disconnection.")
            return False, {}
            
        if soft_limit_hit_info:
            # Format info for display: "Winch X (Top), Winch Y (Bottom)"
            # MoveCode is imported globally
            info_strs = []
            for wid, direction in soft_limit_hit_info.items():
                limit_name = "Top" if direction == MoveCode.UP else "Bottom"
                info_strs.append(f"Winch {wid} ({limit_name})")
            
            print(f"[WARNING] Movement stopped due to Soft Limit on: {', '.join(info_strs)}")
            return False, soft_limit_hit_info
            
        print("Move Complete.")
        self.last_target = (x, y, z)
        return True, {}

    async def nudge_override(self, wids_dict, duration=1.0):
        """
        Performs a blind 'nudge' using Override commands for a short duration.
        Used to move past soft limits when regular moves are rejected.
        wids_dict: {wid: MoveCode.OVERRIDE_UP/DOWN}
        """
        print(f"  [NUDGE] Forcing movement for {duration}s on {list(wids_dict.keys())}...")
        
        # Start Move
        for wid, direction in wids_dict.items():
            if wid in self.clients:
                # Bypass move() logic to avoid monitor checks
                self.clients[wid]._target_speed = 60 # Safe slow-ish speed
                self.clients[wid]._target_move_code = direction
        
        await asyncio.sleep(duration)
        
        # Stop
        for wid in wids_dict.keys():
            if wid in self.clients:
                await self.clients[wid].stop()
        
        print("  [NUDGE] Complete.")
    
    def find_safe_boundary(self, target_x, target_y, z):
        """
        Finds the furthest safe point on the line from Center to (target_x, target_y) at height z.
        """
        cx, cy = self.width / 2.0, self.length / 2.0
        
        # 1. Check if target is already safe
        if self.is_safe(target_x, target_y, z)[0]:
            return target_x, target_y
            
        # 2. Binary search along the ray
        # Parametric line: P(t) = Center + t * (Target - Center)
        # t in [0, 1]
        
        low = 0.0
        high = 1.0
        best_t = 0.0
        
        # Iterations for precision ~1cm
        for _ in range(10): 
            mid = (low + high) / 2.0
            tx = cx + mid * (target_x - cx)
            ty = cy + mid * (target_y - cy)
            
            if self.is_safe(tx, ty, z)[0]:
                best_t = mid
                low = mid
            else:
                high = mid
                
        # Return best safe point
        final_x = cx + best_t * (target_x - cx)
        final_y = cy + best_t * (target_y - cy)
        return final_x, final_y

    def find_max_height(self, x, y):
        """
        Calculates the maximum safe height Z for a given (x, y) position
        based on the safe_angle_deg constraint.
        """
        # We need: H - z >= dist_to_anchor / tan(angle)
        # So: z <= H - dist_to_anchor / tan(angle)
        
        max_tan = math.tan(math.radians(self.safe_angle_deg))
        
        # We must satisfy constraint for ALL anchors.
        # The constraint is dominated by the anchor FURTHEST from (x,y).
        max_horiz_dist = 0.0
        for wid, anchor in self.anchors.items():
            ax, ay, az = anchor
            d = math.sqrt((x - ax)**2 + (y - ay)**2)
            if d > max_horiz_dist:
                max_horiz_dist = d
                
        min_vertical_dist = max_horiz_dist / max_tan
        
        # Max Z = Height - min_vertical_dist
        max_z = self.height - min_vertical_dist
        
        # Also respect Ceiling Margin
        ceiling_limit = self.height - self.min_ceiling_margin
        
        return min(max_z, ceiling_limit)

    async def _monitor_single_move(self, client, wid, target_pos, direction, speed, abort_event):
        # Trigger simulation movement if applicable
        if self.sim_mode:
            if hasattr(client, 'sim_update_pos'):
                asyncio.create_task(client.sim_update_pos(target_pos, speed))
            
        # Hardware move command
        if not self.sim_mode:
            client.last_error_code = 0 # Clear stale errors
            await client.move(direction, speed=speed)
        
        try:
            while True:
                # 1. Global Abort Check
                if abort_event.is_set():
                    break

                # 2. Connection Check
                if not client._is_connected:
                    print(f"  [STOP] Winch {wid} disconnected! Triggering E-STOP.")
                    abort_event.set()
                    break
                
                # Check for errors (Sim or Real)
                if client.last_error_code == 0x81:
                    is_override = (direction == MoveCode.OVERRIDE_UP or direction == MoveCode.OVERRIDE_DOWN)
                    if not is_override:
                        print(f"  [ERROR] Winch {wid}: Soft Limit Reached (0x81)!")
                        return False, "SOFT_LIMIT"
                    # Else: Ignore 0x81 during override
                
                if client.last_error_code == 0x86:
                        print(f"  [ERROR] Winch {wid}: Hard Limit / End of Travel (0x86)!")
                        abort_event.set() # Stop other winches immediately
                        return False, "HARD_LIMIT"
                
                current_pos = client._last_known_position
                if current_pos is None: break
                
                # Check arrival (Deadband)
                diff = current_pos - target_pos
                if abs(diff) < 200:
                    break
                
                await asyncio.sleep(0.1)

            return True, "OK"

        except Exception as e:
            print(f"Error moving winch {wid}: {e}")
            return False, str(e)
        finally:
            if not self.sim_mode:
                if abort_event.is_set():
                    print(f"  [STOP] Winch {wid} stopping.")
                await client.stop()


# ==========================================
# Main Interactive Loop
# ==========================================

async def main():
    parser = argparse.ArgumentParser(description="Cable Robot Interactive Demo")
    parser.add_argument("--config", default="pylifter_config.json", help="Config file path")
    parser.add_argument("--sim", action="store_true", help="Run in simulation mode (no hardware)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        print("Debug logging enabled (writing to debug.log)...")
        # Configure Root Logger to capture everything
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # 1. Update existing Console Handler to filtered INFO
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.INFO)
        
        # 2. Add File Handler for DEBUG
        fh = logging.FileHandler("debug.log", mode='w')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s'))
        root_logger.addHandler(fh)
        
        # Set modules to DEBUG
        logging.getLogger("pylifter").setLevel(logging.DEBUG)
        logging.getLogger("cable_robot").setLevel(logging.DEBUG)

    # Load Config
    if not os.path.exists(args.config):
        print("Config file not found.")
        return
        
    with open(args.config, 'r') as f:
        full_config = json.load(f)
        
    robot_config = full_config.get("cable_robot", {})
    # Merge root level devices/calibration into robot_config context for convenience
    robot_config["devices"] = full_config.get("devices", [])
    robot_config["calibration"] = full_config.get("calibration", {})

    robot = CableRobot(robot_config, sim_mode=args.sim)
    
    # Initialize
    await robot.initialize_winches(robot_config)
    
    print("\n--- Cable Robot Ready ---")
    print(f"Dimensions: {robot.width}x{robot.length}x{robot.height} cm")
    print(f"Home (Center): {robot.width/2}, {robot.length/2}, {robot.height/2}")
    
    while True:
        cmd_str = await asyncio.get_event_loop().run_in_executor(None, input, "\nRobot> ")
        parts = cmd_str.strip().upper().split()
        if not parts: continue
        
        cmd = parts[0]
        
        if cmd == 'Q' or cmd == 'QUIT':
            break

        if cmd == 'HELP' or cmd == '?':
            print("Available Commands:")
            print("  GOTO X Y Z [SPEED]  - Move payload to coordinates (cm). Default Speed=100.")
            print("  GOTO HOME           - Alias for moving to center home position.")
            print("  HOME                - Move to center home position.")
            print("  TESTPATTERN         - Run a 10-point test sequence to verify range of motion.")
            print("  V / VISUALIZE       - Launch 3D visualization window.")
            print("  STATUS              - Show current cable lengths and connection status.")
            print("  TEST_IK X Y Z       - Test inverse kinematics/safety without moving.")
            print("  QUIT / Q            - Exit")
            continue
            
        if cmd == 'GOTO':
            if len(parts) > 1 and parts[1] == 'HOME':
                # Alias for HOME command
                tx, ty, tz = robot.width/2, robot.length/2, robot.height/2
                await robot.move_to(tx, ty, tz, speed=100)
                continue

            if len(parts) < 4:
                print("Usage: GOTO X Y Z [SPEED] or GOTO HOME")
                continue
            try:
                x = float(parts[1])
                y = float(parts[2])
                z = float(parts[3])
                spd = int(parts[4]) if len(parts) > 4 else 100
                
                await robot.move_to(x, y, z, speed=spd)
            except ValueError:
                print("Invalid coordinates.")

        elif cmd == 'HOME':
            # Go to center at mid-height (or defined home)
            tx, ty, tz = robot.width/2, robot.length/2, robot.height/2
            await robot.move_to(tx, ty, tz, speed=100)

        elif cmd == 'STATUS':
            print("Status:")
            for wid, c in robot.clients.items():
                status_str = "Connected" if c._is_connected else "DISCONNECTED"
                print(f"  ID {wid}: {c.current_distance:.1f} cm (Pos: {c._last_known_position}) [{status_str}]")
                
        elif cmd == 'TEST_IK':
            # Debug tool to check IK and Safety calculations without moving
            if len(parts) < 4:
                print("Usage: TEST_IK X Y Z")
                continue
            try:
                x = float(parts[1])
                y = float(parts[2])
                z = float(parts[3])
                
                is_safe, msg = robot.is_safe(x, y, z)
                print(f"Point ({x}, {y}, {z}): Safety = {is_safe} ({msg})")
                
                if is_safe:
                    lens = robot.inverse_kinematics(x, y, z)
                    print("Required Lengths:")
                    for wid, l in lens.items():
                        print(f"  ID {wid}: {l:.2f} cm")
            except ValueError:
                print("Invalid coords.")

        elif cmd == 'TESTPATTERN':
            print("Running Test Pattern...")
            
            # Define Heights
            z_low = robot.min_floor_margin
            z_high = robot.height - robot.min_ceiling_margin
            
            # Define Base Corners (Physical)
            # FL=(0,0), BL=(0,L), BR=(W,L), FR=(W,0)
            # Note: User request: FL, BL, BR, FR
            w, l = robot.width, robot.length
            cx, cy = w/2.0, l/2.0
            
            try:
                pattern_speed = int(parts[1]) if len(parts) > 1 else 100
            except ValueError:
                print("Invalid speed provided. Using default 100.")
                pattern_speed = 100

            # Helper to generate waypoint
            async def run_point(name, tx, ty, tz, speed_val):
                # Clamp to safe zone
                sx, sy = robot.find_safe_boundary(tx, ty, tz)
                print(f"--> Waypoint: {name} ({sx:.1f}, {sy:.1f}, {tz:.1f})")
                
                success, failed_wids = await robot.move_to(sx, sy, tz, speed=speed_val)
                
                # Check for Soft Limit Failure logic
                if not success and failed_wids: 
                    # failed_wids is now a dict {wid: direction}
                    
                    # MoveCode is imported globally
                    info_strs = []
                    for wid, direction in failed_wids.items():
                        limit_name = "Top" if direction == MoveCode.UP else "Bottom"
                        info_strs.append(f"Winch {wid} ({limit_name})")
                        
                    print(f"    [!] Movement to {name} incomplete. Soft Limit on: {', '.join(info_strs)}")
                    val = await asyncio.get_event_loop().run_in_executor(None, input, "    Soft limit hit? Expand limits? (Y/N): ")
                    if val.lower() == 'y':
                        print(f"    Expanding Limits on Winches {list(failed_wids.keys())}...")
                        from pylifter.protocol import SmartPointCode
                        
                        # 1. Determine which limits to update AFTER the move
                        points_to_set = {} # {wid: point_code}
                        
                        for wid, direction in failed_wids.items():
                            # If we were moving UP (Retract) -> New Top Limit
                            if direction == MoveCode.UP:
                                points_to_set[wid] = SmartPointCode.TOP
                            else:
                                points_to_set[wid] = SmartPointCode.BOTTOM

                        print("    Clearing Limits on failed winches...")
                        from pylifter.protocol import SmartPointCode
                        
                        # 2. Clear Limits
                        for wid in failed_wids.keys():
                            if wid in robot.clients:
                                print(f"    [Winch {wid}] Clearing Soft Limits (Top & Bottom)...")
                                await robot.clients[wid].clear_smart_point(SmartPointCode.TOP)
                                await robot.clients[wid].clear_smart_point(SmartPointCode.BOTTOM)
                        
                        await asyncio.sleep(1.0)
                        
                        # 3. Retry Standard Move
                        print("    Limits Cleared. Retrying Standard Move...")
                        success, _ = await robot.move_to(sx, sy, tz, speed=speed_val)
                        
                        if success:
                            print("    Move Successful. Setting new Soft Limits...")
                            # 4. Set New Limits
                            for wid, pt in points_to_set.items():
                                if wid in robot.clients:
                                     await robot.clients[wid].set_smart_point(pt)
                            print("    New Limits Set.")
                        else:
                            print("    Retry failed even after clearing limits.")
                            
                if success:
                    await asyncio.sleep(1.0)
                return success

            steps = [
                ("Home", cx, cy, robot.height/2.0),
                ("Front-Left Lower", 0, 0, z_low),
                ("Back-Left Lower", 0, l, z_low),
                ("Back-Right Lower", w, l, z_low),
                ("Front-Right Lower", w, 0, z_low),
                ("Front-Right Upper", w, 0, z_high),
                ("Front-Left Upper", 0, 0, z_high),
                ("Back-Left Upper", 0, l, z_high),
                ("Back-Right Upper", w, l, z_high),
                ("Center High", cx, cy, z_high),
                ("Home", cx, cy, robot.height/2.0)
            ]
            
            for name, tx, ty, tz in steps:
                # SPECIAL LOGIC: For Upper Corners, prioritize X/Y even if it means lowering Z.
                if "Upper" in name:
                    # Recalculate max safe Z for this X/Y
                    safe_z = robot.find_max_height(tx, ty)
                    # Use the lower of the two: the config z_high or the calculated safe_z
                    # Actually, if safe_z < z_high, we MUST usage safe_z.
                    # If safe_z > z_high, we utilize z_high (ceiling margin).
                    # find_max_height already clamps to ceiling margin.
                    tz = safe_z
                    
                if not await run_point(name, tx, ty, tz, pattern_speed):
                    print("Test Pattern Aborted.")
                    break
            
            print("Test Pattern Complete.")

        elif cmd == 'V' or cmd == 'VISUALIZE':
            try:
                import subprocess
                
                # Check if we have a last target
                if not hasattr(robot, 'last_target'):
                     robot.last_target = (robot.width/2, robot.length/2, robot.height/2)
                
                lx, ly, lz = robot.last_target
                pos_str = f"{lx},{ly},{lz}"
                
                print(f"Launching Visualizer for position {pos_str}...")
                
                # Run the plotter as a separate process
                # We assume cable_robot_plot.py is in the same directory
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cable_robot_plot.py")
                
                subprocess.Popen([sys.executable, script_path, "--config", args.config, "--pos", pos_str])
                print("") # Add spacing before next prompt
                
            except Exception as e:
                print(f"Error launching visualizer: {e}")

    # Cleanup
    print("Disconnecting...")
    for c in robot.clients.values():
        await c.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
