
import asyncio
import logging
import struct
from typing import Optional, Callable
from bleak import BleakClient, BleakScanner
from .protocol import *

logger = logging.getLogger(__name__)

class PyLifterClient:
    def __init__(self, mac_address: str, passkey: Optional[str] = None):
        self.mac_address = mac_address
        self._passkey: Optional[bytes] = bytes.fromhex(passkey) if passkey else None
        self._client: Optional[BleakClient] = None
        self._auth_event = asyncio.Event()

        self._notification_callbacks = []
        self._stats_future: Optional[asyncio.Future] = None
        
        # State for Keep-Alive Loop
        self._polling_task: Optional[asyncio.Task] = None
        self._target_move_code: MoveCode = MoveCode.STOP
        self._target_speed: int = 0
        self._is_connected = False
        
        # Internal state
        # Initialize to None to indicate we haven't synced with device yet
        self._last_known_position: Optional[int] = None 
        self.last_error_code: int = 0 
        self._last_logged_error_code: int = -1 # For suppressing duplicate logs 
        
        # Calibration state (Linear: Distance = Slope * Position + Intercept)
        self._cal_slope: float = 0.0
        self._cal_intercept: float = 0.0 

    @property
    def passkey(self) -> Optional[bytes]:
        return self._passkey 
        
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

    async def connect(self):
        logger.info(f"Connecting to {self.mac_address}...")
        self._client = BleakClient(self.mac_address)
        await self._client.connect()
        self._is_connected = True 
        logger.info("Connected.")
        
        await self._client.start_notify(RESPONSE_CHAR_UUID, self._notification_handler)
        logger.info("Notifications enabled. Starting Handshake...")

        # 1. Authenticate & Start Keep-Alive IMMEDIATELY
        logger.info("Handshake: Authenticating...")
        await self._authenticate()
        
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
                
                try:
                    # logger.debug(f"TX PKT: Move={self._target_move_code}, Speed={self._target_speed}, Pos={pos}")
                    await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
                except Exception as e:
                    logger.warning(f"Keep-Alive Write Failed: {e}")
                
                await asyncio.sleep(0.1) # 10Hz - Match Raw Script Timing for Stability
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Keep-Alive Loop Error: {e}")

    async def _authenticate(self):
        self._auth_event.clear()
        
        if self._passkey:
            logger.info("Sending Passkey...")
            packet = build_packet(CommandCode.SET_PASSKEY, self._passkey)
            await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
            
            # Start Keep-Alive Loop IMMEDIATELY
            if self._polling_task is None:
                 logger.info("Starting Keep-Alive Loop (Post-Passkey)...")
                 self._polling_task = asyncio.create_task(self._keep_alive_loop())
            
            logger.info("Passkey sent. Loop started.")

        else:
             logger.error("No passkey provided! cannot authenticate.")

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
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def get_stats(self):
        self._stats_future = asyncio.get_event_loop().create_future()
        packet = build_packet(CommandCode.GET_STATS)
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
        return await asyncio.wait_for(self._stats_future, timeout=3.0)

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
        
        elif cmd == CommandCode.MOVE:
            # Payload: 8 bytes.
            payload = data[2:]
            if len(payload) >= 8:
                move_status, error_code, pos, weight = struct.unpack("<B B i H", payload[:8])
                
                # CRITICAL: Always update position from device feedback
                self._last_known_position = pos
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
                         else:
                             logger.error(f"MOVE returned Error Code: {error_code} at Pos={pos}")
                     self._last_logged_error_code = error_code
                 
                # Reset logged error if status returns to normal (0)
                if error_code == 0:
                    self._last_logged_error_code = 0
            else:
                logger.warning(f"MOVE Response too short: {data.hex()}")

    async def set_calibration(self, code: int = 1):
        logger.info(f"Sending SET_CALIBRATION (Code={code})...")
        packet = build_packet(CommandCode.CALIBRATE, struct.pack("B", code))
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def clear_error(self):
        logger.info("Sending CLEAR_ERROR...")
        packet = build_packet(CommandCode.CLEAR_ERROR)
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
        
        # Reset local error state immediately
        self.last_error_code = 0
        self._last_logged_error_code = 0

    async def go_override(self):
        logger.info("Sending GO_OVERRIDE...")
        packet = build_packet(CommandCode.GO_OVERRIDE)
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
