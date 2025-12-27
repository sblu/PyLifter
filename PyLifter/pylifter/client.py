
import asyncio
import logging
import struct
from typing import Optional, Callable
from bleak import BleakClient, BleakScanner
from .protocol import *

logger = logging.getLogger(__name__)

TESTED_FIRMWARE_VERSIONS = ["3.2"]

class PyLifterClient:
    def __init__(self, mac_address: str, passkey: Optional[str] = None):
        self.mac_address = mac_address
        self._passkey: Optional[bytes] = bytes.fromhex(passkey) if passkey else None
        self._client: Optional[BleakClient] = None
        self._auth_event = asyncio.Event()
        self._write_lock = asyncio.Lock() # Serialize GATT writes
        
        self._notification_callbacks = []
        self._notification_callbacks = []
        self._stats_future: Optional[asyncio.Future] = None
        self._version_future: Optional[asyncio.Future] = None
        self._proto_version_future: Optional[asyncio.Future] = None
        
        # State for Keep-Alive Loop
        self._polling_task: Optional[asyncio.Task] = None
        self._target_move_code: MoveCode = MoveCode.STOP
        self._target_speed: int = 0
        self._is_connected = False
        
        # Internal state
        # Initialize to None to indicate we haven't synced with device yet
        self._last_known_position: Optional[int] = None 
        self._last_known_weight: int = 0
        self.last_error_code: int = 0 
        self._last_logged_error_code: int = -1 # For suppressing duplicate logs 
        
        # Calibration state (Linear: Distance = Slope * Position + Intercept)
        self._cal_slope: float = 0.0
        self._cal_intercept: float = 0.0 
        # Connect management
        self._is_connected = False
        self._suppress_disconnect_callbacks = False

    @property
    def passkey(self) -> Optional[bytes]:
        return self._passkey 
        
    @property
    def current_weight(self) -> int:
        """Returns the last reported weight load (raw unit)."""
        return self._last_known_weight

    @property
    def current_distance(self) -> float:
        """Returns the estimated distance in configured units based on calibration."""
        if self._last_known_position is None:
            return 0.0
        return (self._cal_slope * self._last_known_position) + self._cal_intercept

    def set_unit_calibration(self, slope: float, intercept: float):
        """Sets the linear calibration factors (y = mx + b)."""
        self._cal_slope = slope
        self._cal_intercept = intercept
        logger.info(f"Calibration Set: Dist = {slope:.5f} * Pos + {intercept:.2f}")

    def _on_disconnect(self, client: BleakClient):
        """Callback for when Bleak detects a disconnect."""
        if self._suppress_disconnect_callbacks:
            logger.debug(f"Suppressed Disconnect Callback for {self.mac_address} (During Connect/Retry)")
            return
            
        if not self._suppress_disconnect_callbacks:
            logger.info(f"Bleak Disconnected Callback for {self.mac_address}")
            self._is_connected = False

    async def _establish_connection(self):
        """Internal helper to create connection, auth, and setup notifications."""
        # 1. Clean up potential zombie checks
        if self._client and self._client.is_connected:
            logger.info("Closing previous connection before retry...")
            await self._client.disconnect()
            await asyncio.sleep(0.5) # Allow BlueZ to cleanup
            
        # 2. Add settle delay to let BlueZ/Adapter recover from scan or previous abort
        await asyncio.sleep(1.0) 
            
        logger.info(f"Initiating connection to {self.mac_address} (Timeout=20s)...")
        # Initialize with callback, but suppress it initially
        self._suppress_disconnect_callbacks = True
        self._client = BleakClient(
            self.mac_address, 
            disconnected_callback=self._on_disconnect,
            timeout=20.0
        ) 
        
        try:
            # 3. Connect (Bleak handles Service Discovery internally)
            await self._client.connect()
            # Connection successful - enable callback
            self._suppress_disconnect_callbacks = False
            
        except Exception as e:
            logger.warning(f"Connection Failed: {e}. Attempting Cache Scrub via bluetoothctl...")
            if self._client and self._client.is_connected:
                 await self._client.disconnect()
            
            # 3a. Force remove device from BlueZ cache
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "remove", self.mac_address,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                logger.info("Device removed from BlueZ cache.")
            except Exception as scrub_err:
                 logger.error(f"Failed to scrub device: {scrub_err}")
            
            await asyncio.sleep(2.0) # Wait for BlueZ to reset
            
            # 3b. Retry Connection
            logger.info("Retrying connection after scrub...")
            self._suppress_disconnect_callbacks = True
            self._client = BleakClient(
                self.mac_address, 
                disconnected_callback=self._on_disconnect,
                timeout=20.0
            ) 
            await self._client.connect()
            self._suppress_disconnect_callbacks = False

        self._is_connected = True 
        
        await self._client.start_notify(RESPONSE_CHAR_UUID, self._notification_handler)
        
        # 4. Authenticate & Start Keep-Alive IMMEDIATELY
        await self._authenticate()

    async def connect(self, wait_for_position: bool = True):
        logger.info(f"Connecting to {self.mac_address}...")
        
        # Use helper with parameters
        await self._establish_connection()
        logger.info("Connected & Authenticated. Starting Keep-Alive...")
        
        # Auth and notification start is now inside _establish_connection
        pass
        
        if wait_for_position:
            # 2. Wait for initial position sync
            logger.info("Waiting for initial position sync...")
            for _ in range(20): # Wait up to 2 seconds
                if self._last_known_position is not None:
                    logger.info(f"Initial position synced: {self._last_known_position}")
                    break
                await asyncio.sleep(0.1)
                
            if self._last_known_position is None:
                logger.warning("Initial position not received. Defaulting to 0 (Risky - May cause Sync Error).")
                self._last_known_position = 0
        else:
            logger.info("Skipping initial position sync (Pairing Mode).")
            # Initialize to 0 strictly to avoid type errors if accessed, 
            # though we shouldn't use this client for moves.
            if self._last_known_position is None:
                self._last_known_position = 0
        
        logger.info("Authenticated and Ready.")

    async def disconnect(self):
        self._is_connected = False
        
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            
        if self._client:
             try:
                 await self._client.stop_notify(RESPONSE_CHAR_UUID)
                 await self._client.disconnect()
             except Exception as e:
                 logger.error(f"Error disconnecting: {e}")
             self._client = None
             logger.info("Disconnected.")

    async def _keep_alive_loop(self):
        logger.info("Keep-Alive Loop Started.")
        try:
            service_fail_count = 0
            while self._is_connected:
                # Build packet based on current state
                # ALWAYS use _last_known_position to prevent Sync Errors
                pos = self._last_known_position if self._last_known_position is not None else 0
                
                # Safety check: if we are trying to move but pos is None (unlikely due to connect wait), we might trigger error.
                
                packet = build_move_packet(
                    self._target_move_code, 
                    speed=self._target_speed,
                    avg_pos=pos
                )
                
                if self._client and self._client.is_connected:
                    try:
                        # logger.debug(f"TX PKT: Move={self._target_move_code}, Speed={self._target_speed}, Pos={pos}")
                        
                        # Only send if lock is available (don't block keep-alive on long ops)
                        if not self._write_lock.locked():
                            async with self._write_lock:
                                await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
                                # Throttle: Give the stack a moment to breathe
                                await asyncio.sleep(0.02)
                                
                            service_fail_count = 0 # Reset on successful write
                            
                    except Exception as e:
                        err_str = str(e)
                        if "Service Discovery" in err_str:
                            # Transient startup error or connection loss.
                            service_fail_count += 1
                            if service_fail_count > 5:
                                logger.error(f"Keep-Alive Stopped (Max Retries): {err_str}")
                                self._is_connected = False
                                break
                            else:
                                logger.warning(f"Keep-Alive Service Discovery Error - Reconnecting (Attempt {service_fail_count}/5)...")
                                try:
                                    # Attempt transparent reconnection
                                    await self._establish_connection()
                                    logger.info("Transparent Reconnection Successful.")
                                    service_fail_count = 0 # Reset counter
                                    continue # Resume loop immediately
                                except Exception as rec_err:
                                    logger.error(f"Reconnection Attempt Failed: {rec_err}")
                                    await asyncio.sleep(1.0)
                                    continue
                        else:
                            # For other errors, just warn
                             logger.warning(f"Keep-Alive Write Failed: {e}")
                
                if self._target_move_code != MoveCode.STOP:
                    await asyncio.sleep(0.2) # 5Hz when moving (Responsive)
                else:
                    await asyncio.sleep(0.25) # 4Hz when idle (Stable, Standard)
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Keep-Alive Loop Error: {e}")

    async def write_command(self, packet: bytes, response: bool = True):
        """Helper to safely write commands with lock and throttling."""
        async with self._write_lock:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=response)
             # Throttle: Give the stack a moment to breathe
             await asyncio.sleep(0.02)

    async def _authenticate(self):
        self._auth_event.clear()
        
        if self._passkey:
            logger.info("Sending Passkey...")
            packet = build_packet(CommandCode.SET_PASSKEY, self._passkey)
            # Use the throttled helper if possible, or direct for now since _authenticate uses direct writes
            await self.write_command(packet, response=False)
            
            # Start Keep-Alive Loop IMMEDIATELY
            if self._polling_task is None:
                 logger.info("Starting Keep-Alive Loop (Post-Passkey)...")
                 self._polling_task = asyncio.create_task(self._keep_alive_loop())
            
            logger.info("Passkey sent. Loop started.")

        else:
             logger.info("No passkey provided. Sending GET_PASSKEY request...")
             
             # Send GET_PASSKEY (0x03, empty payload)
             packet = build_packet(CommandCode.GET_PASSKEY)
             await self.write_command(packet, response=False)
             
             logger.info("Waiting for button press on Winch...")
             # Wait for the notification handler to receive GET_PASSKEY and set self._passkey
             # We can wait on a condition or just return and let logic flow?
             # But connect() waits for _authenticate().
             
             # We should wait here until we have a passkey, or timeout.
             try:
                 # We need a new event for "Passkey Received" separate from "Auth Complete"?
                 # Actually, _notification_handler sets _passkey and calls _send_set_passkey, which eventually sets _auth_event.
                 # So waiting for _auth_event might be enough IF the device sends the passkey.
                 # But _auth_event is set when SET_PASSKEY is ACKed.
                 
                 # Logic check:
                 # 1. User presses button.
                 # 2. Device sends 0x41 (GET_PASSKEY) with payload.
                 # 3. Handler extracts passkey, updates self._passkey.
                 # 4. Handler spawns _send_set_passkey.
                 # 5. Client sends SET_PASSKEY.
                 # 6. Device sends ACK.
                 # 7. Handler sets _auth_event.
                 
                 # So yes, we can just wait for _auth_event, but with a longer timeout for user action.
                 await asyncio.wait_for(self._auth_event.wait(), timeout=30.0) 
                 logger.info("Pairing Successful (Passkey Received).")
             except asyncio.TimeoutError:
                 logger.error("Pairing Timed Out: Button not pressed?")

    async def move(self, direction: MoveCode, speed: int = 100):
        """Updates the target state. The keep-alive loop handles transmission."""
        if not self._is_connected:
             raise RuntimeError("Not connected")
             
        self._target_move_code = direction
        self._target_speed = speed
        
        # We allow immediate "send" optimization for responsiveness if needed, but the loop is fast enough.
        # Just updating state is safer to avoid race conditions on write_gatt_char.

    async def stop(self):
        """Stops the winch."""
        self._target_move_code = MoveCode.STOP
        self._target_speed = 0
        
        # Send immediately for responsiveness
        # CRITICAL: Must echo last known position to avoid Sync Error
        pos = self._last_known_position if self._last_known_position is not None else 0
        packet = build_move_packet(MoveCode.STOP, speed=0, avg_pos=pos)
        
        if self._client and self._is_connected:
            try:
                await self.write_command(packet, response=False)
            except Exception as e:
                # If immediate stop fails (e.g. Service Discovery error), just warn.
                # The keep-alive loop will pick up the new _target_move_code = STOP shortly.
                logger.warning(f"Immediate Stop Write Failed: {e}")

    async def get_stats(self):
        self._stats_future = asyncio.get_event_loop().create_future()
        packet = build_packet(CommandCode.GET_STATS)
        await self.write_command(packet, response=True)
        return await asyncio.wait_for(self._stats_future, timeout=3.0)

    async def get_version(self):
        self._version_future = asyncio.get_event_loop().create_future()
        packet = build_packet(CommandCode.GET_VERSION)
        await self.write_command(packet, response=True)
        return await asyncio.wait_for(self._version_future, timeout=3.0)

    async def get_protocol_version(self):
        self._proto_version_future = asyncio.get_event_loop().create_future()
        packet = build_packet(CommandCode.GET_PROTOCOL_VERSION)
        await self.write_command(packet, response=True)
        return await asyncio.wait_for(self._proto_version_future, timeout=3.0)

    def _notification_handler(self, sender, data):
        # logger.debug(f"RX: {data.hex()}")
        if not data:
            return

        cmd = data[0]
        
        # Authentication Handshake
        if cmd == CommandCode.GET_PASSKEY:
            if len(data) >= 8:
                received_passkey = data[2:8]
                logger.debug(f"Device Passkey: {received_passkey.hex()}")
                self._passkey = received_passkey # Update stored passkey
                asyncio.create_task(self._send_set_passkey(received_passkey))
        
        elif cmd == CommandCode.ACK:
            if len(data) >= 3:
                acked_cmd = data[2]
                if acked_cmd == CommandCode.SET_PASSKEY:
                    self._auth_event.set()

        elif cmd == CommandCode.GET_STATS:
            data_len = len(data)
            if data_len >= 20: 
                payload = data[2:]
                _, total_time, _, _, _, err_cnt, err_classes = struct.unpack("<H I H H H H I", payload[:18])
                if err_classes != 0:
                    logger.warning(f"GET_STATS: Error Classes Bitmask: 0x{err_classes:08X}")
                logger.info(f"Stats: Time={total_time}, ErrCnt={err_cnt}, ErrMask={err_classes}")
            else:
                logger.warning(f"GET_STATS Response too short: {data.hex()}")

            if self._stats_future and not self._stats_future.done():
                self._stats_future.set_result(data)
        
        elif cmd == CommandCode.GET_VERSION:
            if len(data) >= 10: # We need at least up to byte 7 (firmware.major) + 2 wrapper? No, payload is data[2:]
                # Packet: [Cmd][Len][Payload...]
                # Payload based on Java:
                # 0: hw.minor
                # 1: hw.major
                # 2: hw.ver
                # 3: factory_tag
                # 4-5: unknown1
                # 6: fw.minor
                # 7: fw.major
                
                payload = data[2:]
                if len(payload) >= 8:
                    try:
                        hw_min, hw_maj, hw_ver, fac_tag, _, fw_min, fw_maj = struct.unpack("<BBBB H BB", payload[:8])
                        
                        # Firmware Version = fw_maj.fw_min (e.g. 3.1)
                        # Hardware Version = hw_maj.hw_min.hw_ver
                        
                        version_str = f"{fw_maj}.{fw_min}" # Matches App display style (3.1)
                        # Optionally include build/etc if needed, but App seems to show X.Y
                        
                        logger.info(f"Firmware Version: {version_str} (HW: {hw_maj}.{hw_min}.{hw_ver})")
                        
                        if self._version_future and not self._version_future.done():
                            self._version_future.set_result(version_str)
                    except Exception as e:
                         logger.warning(f"GET_VERSION Parse Error: {e}")
                else:
                    logger.warning(f"GET_VERSION Payload too short: {len(payload)}")
            else:
                 logger.warning(f"GET_VERSION Response too short: {data.hex()}")
        
        elif cmd == CommandCode.GET_PROTOCOL_VERSION:
            # Payload: 1 byte "version"
            if len(data) >= 3: 
                payload = data[2:]
                try:
                    raw_ver = payload[0]
                    # Guessing Nibble encoding: 0x41 -> 4.1
                    maj = (raw_ver >> 4) & 0x0F
                    min_ = raw_ver & 0x0F
                    ver_str = f"{maj}.{min_}"
                        
                    logger.info(f"Protocol Version: {ver_str} (Raw: 0x{raw_ver:02X})")
                    if self._proto_version_future and not self._proto_version_future.done():
                        self._proto_version_future.set_result(ver_str)
                except:
                     if self._proto_version_future: self._proto_version_future.set_result("Unknown")
            else:
                logger.warning(f"GET_PROTOCOL_VERSION Response too short: {data.hex()}")
        
        elif cmd == CommandCode.MOVE:
            # Payload: 8 bytes.
            payload = data[2:]
            if len(payload) >= 8:
                move_status, error_code, pos, weight = struct.unpack("<B B i H", payload[:8])
                
                # CRITICAL: Always update position from device feedback
                self._last_known_position = pos
                self._last_known_weight = weight
                self.last_error_code = error_code
                # logger.debug(f"RX POS update: {pos}")
                
                # Check if we should log this error (suppress duplicates)
                if error_code != self._last_logged_error_code:
                     if error_code != 0:
                         if error_code == 0x86:
                             logger.warning(f"End of Travel Reached (0x86) at Pos={pos}")
                         elif error_code == 0x09:
                             logger.error(f"Sync Error (0x09)! DevicePos={pos}, ClientLastKnown={self._last_known_position}")
                         elif error_code == 0x81: # WarningSoftLimit
                             logger.warning(f"Soft Limit Reached (0x81) at Pos={pos}")
                         elif error_code == 0x83: # ErrorSmartPointNotSet
                             logger.warning(f"Enable to Move: Smart Point Not Set (0x83) at Pos={pos}")
                         else:
                             logger.error(f"MOVE returned Error Code: {error_code} at Pos={pos}")
                     self._last_logged_error_code = error_code
                 
                # Reset logged error if status returns to normal (0)
                if error_code == 0:
                    self._last_logged_error_code = 0
            else:
                logger.warning(f"MOVE Response too short: {data.hex()}")

    async def set_calibration(self, code: int = 1):
        """Deprecated: Use set_smart_point instead."""
        await self.set_smart_point(SmartPointCode(code))

    async def set_smart_point(self, point: SmartPointCode):
        logger.info(f"Setting Smart Point: {point.name} ({point.value})...")
        packet = build_packet(CommandCode.CALIBRATE, struct.pack("B", point.value))
        async with self._write_lock:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def clear_smart_point(self, point: SmartPointCode):
        logger.info(f"Clearing Smart Point: {point.name} ({point.value})...")
        packet = build_clear_smart_point_packet(point)
        async with self._write_lock:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
        
    async def override_move(self, direction: MoveCode, speed: int = 100):
        """
        Sends GO_OVERRIDE (0x25) to move past limits.
        """
        self._target_move_code = direction
        self._target_speed = speed
        
        pos = self._last_known_position if self._last_known_position is not None else 0
        payload = struct.pack("<BBi", direction, speed, pos)
        packet = build_packet(CommandCode.GO_OVERRIDE, payload)
        
        if self._client and self._is_connected:
             # logger.debug(f"TX OVERRIDE: Dir={direction}, Pos={pos}")
             async with self._write_lock:
                 await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def clear_error(self):
        logger.info("Sending CLEAR_ERROR...")
        packet = build_packet(CommandCode.CLEAR_ERROR)
        
        # Protect against Service Discovery errors during crash recovery
        try:
            async with self._write_lock:
                await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
        except Exception as e:
                logger.warning(f"clear_error failed (Ignored): {e}")

        
        # Reset local error state immediately
        self.last_error_code = 0
        self._last_logged_error_code = 0

    async def go_override(self):
        logger.info("Sending GO_OVERRIDE...")
        packet = build_packet(CommandCode.GO_OVERRIDE)
        async with self._write_lock:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
        
        # Reset local error state
        self.last_error_code = 0
        self._last_logged_error_code = 0
    
    async def factory_calibrate(self, code: int = 1):
        logger.info(f"Sending FACTORY_CALIBRATE (Code={code})...")
        packet = build_packet(CommandCode.FACTORY_CALIBRATE, struct.pack("B", code))
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def clear_calibration(self, code: int = 1):
        logger.info(f"Sending CLEAR_CALIBRATION (Code={code})...")
        packet = build_packet(CommandCode.CLEAR_CALIBRATION, struct.pack("B", code))
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def _send_set_passkey(self, passkey: bytes):
        packet = build_packet(CommandCode.SET_PASSKEY, passkey)
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
