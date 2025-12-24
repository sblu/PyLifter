
import asyncio
import logging
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
        self._last_known_position = 0   # Match Raw Script (0)
        
    async def connect(self):
        logger.info(f"Connecting to {self.mac_address}...")
        self._client = BleakClient(self.mac_address)
        await self._client.connect()
        self._is_connected = True # CRITICAL FIX: Enable state for Keep-Alive Loop
        logger.info("Connected.")
        
        await self._client.start_notify(RESPONSE_CHAR_UUID, self._notification_handler)
        logger.info("Notifications enabled. Starting Handshake...")

        # 1. Authenticate & Start Keep-Alive IMMEDIATELY
        # The device has a strict watchdog. We must auth and start sending packets ASAP.
        logger.info("Handshake: Authenticating...")
        await self._authenticate()
        
        # 2. Fetch Metadata (SKIPPED FOR STABILITY)
        # The raw script proves that ONLY sending Keep-Alive packets is safe.
        # Sending other commands (Get Name, Stats) might trigger error states or timing issues.
        # We will expose these as manual methods if needed, but NOT auto-run them.
        
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
                # TAKE 16: Sequential Start + 8-Byte Packet + Speed 0 (handled by target_speed)
                # App sends: STOP (0), SPEED (0), POS (0) initially.
                # stop() sets _target_speed=0.
                
                packet = build_move_packet(
                    self._target_move_code, 
                    speed=self._target_speed,
                    avg_pos=self._last_known_position
                )
                
                try:
                    # CRITICAL: Use response=False (Write Command)
                    await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
                    # logger.debug(f"KA Sent: {packet.hex()}")
                except Exception as e:
                    logger.warning(f"Keep-Alive Write Failed: {e}")
                
                await asyncio.sleep(0.05) # TAKE: 18 - 50ms to beat 200ms watchdog safely.
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Keep-Alive Loop Error: {e}")

    async def _authenticate(self):
        self._auth_event.clear()
        
        if self._passkey:
            logger.info(f"Sending SET_PASSKEY directly: {self._passkey.hex()}")
            
            # TAKE 26: Strict Strict Ordering (Match Raw Script)
            # 1. Write Passkey FIRST (Blocking await) to ensure it is the very first packet.
            logger.info("Sending Passkey...")
            packet = build_packet(CommandCode.SET_PASSKEY, self._passkey)
            await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)
            
            # 2. Start Keep-Alive Loop IMMEDIATELY after Passkey is on the wire.
            if self._polling_task is None:
                 logger.info("Starting Keep-Alive Loop (Post-Passkey)...")
                 self._polling_task = asyncio.create_task(self._keep_alive_loop())
            
            logger.info("Passkey sent. Loop started. Assuming Auth Success.")

        else:
             logger.error("No passkey provided! cannot authenticate.")

    async def move(self, direction: MoveCode, speed: int = 100):
        """Updates the target state. The keep-alive loop handles transmission."""
        self._target_move_code = direction
        self._target_speed = speed
        # Send immediately for responsiveness
        packet = build_move_packet(direction, speed=speed)
        if self._client and self._is_connected:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def stop(self):
        """Stops the winch."""
        self._target_move_code = MoveCode.STOP
        self._target_speed = 0
        pass # Loop handles transmission
        
        # Send immediately for responsiveness (Full Packet, No Response)
        packet = build_move_packet(MoveCode.STOP)
        if self._client and self._is_connected:
             await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def get_stats(self):
        self._stats_future = asyncio.get_event_loop().create_future()
        packet = build_packet(CommandCode.GET_STATS)
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
        return await asyncio.wait_for(self._stats_future, timeout=3.0)

    def _notification_handler(self, sender, data):
        # DEBUG: Match Raw Script - No Parsing, just log.
        logger.debug(f"RX: {data.hex()}")
        
        # Check for Auth ACK (Command 0x01, Sub 0x03) manually to set event if needed
        # 01 01 03 ...
        if len(data) >= 3 and data[0] == CommandCode.ACK and data[2] == CommandCode.SET_PASSKEY:
             self._auth_event.set()
        
        # IGNORE ALL OTHER PARSING FOR STABILITY TEST
        # if not data:
        #     return

        # cmd = data[0]
        
        # # Authentication Handshake
        # if cmd == CommandCode.GET_PASSKEY:
        #     if len(data) >= 8:
        #         received_passkey = data[2:8]
        #         logger.debug(f"Device Passkey: {received_passkey.hex()}")
        #         # If we have a stored passkey, we could verify? 
        #         # For now, just echo it back as per protocol.
        #         asyncio.create_task(self._send_set_passkey(received_passkey))
        
        # elif cmd == CommandCode.ACK:
        #     if len(data) >= 3:
        #         acked_cmd = data[2]
        #         if acked_cmd == CommandCode.SET_PASSKEY:
        #             self._auth_event.set()

        # elif cmd == CommandCode.GET_STATS:
        #     # TAKE 22: Parse Position from GET_STATS (0x34)
        #     # RX: 34 12 e7 00 d9 07 00 00 ...
        #     # Index: 0=Cmd, 1=Len, 2-3=Stat1, 4-7=Position
        #     # Payload: 18 bytes.
        #     # Structure: Cycles(2), Time(4), MinTemp(2), MaxTemp(2), Reset(2), ErrCnt(2), ErrClasses(4)
        #     data_len = len(data)
        #     if data_len >= 20: # 2 header + 18 payload
        #         payload = data[2:]
        #         # We are only interested in Time/Errors for now.
        #         # Note: Original hypothesis that bytes 4-8 were Position was WRONG. It is TotalTime.
        #         _, total_time, _, _, _, err_cnt, err_classes = struct.unpack("<H I H H H H I", payload[:18])
                
        #         # Log Error Classes bitmask
        #         if err_classes != 0:
        #             logger.warning(f"GET_STATS: Error Classes Bitmask: 0x{err_classes:08X}")
        #             # Bit 10 = SyncTimeout, Bit 16 = VoltageLow
                
        #         logger.info(f"Stats: Time={total_time}, ErrCnt={err_cnt}, ErrMask={err_classes}")
        #         # DO NOT update _last_known_position from this.
        #     else:
        #         logger.warning(f"GET_STATS Response too short: {payload.hex()}")

        #     if self._stats_future and not self._stats_future.done():
        #         self._stats_future.set_result(data)
        
        # elif cmd == CommandCode.MOVE:
        #     # Payload: 8 bytes.
        #     # Structure: Status(1), ErrorCode(1), Position(4), Weight(2)
        #     payload = data[2:]
        #     if len(payload) >= 8:
        #         move_status, error_code, pos, weight = struct.unpack("<B B i H", payload[:8])
                
        #         self._last_known_position = pos
        #         # logger.info(f"MOVE Response: Pos={pos}, Status={move_status}, Err={error_code}, WBt={weight}")
                
        #         if error_code != 0:
        #              logger.error(f"MOVE returned Error Code: {error_code} (Check ErrorCode.java)")
        #     else:
        #         logger.warning(f"MOVE Response too short: {data.hex()}")

    async def factory_calibrate(self, code: int = 1):
        """
        Sends a FACTORY_CALIBRATE command.
        :param code: 1=Start, 0=Stop
        """
        logger.info(f"Sending FACTORY_CALIBRATE (Code={code})...")
        packet = build_packet(CommandCode.FACTORY_CALIBRATE, struct.pack("B", code))
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def clear_calibration(self, code: int = 1):
        """
        Sends a CLEAR_CALIBRATION command.
        :param code: 1=Start (Generic?), 0=Stop?
        """
        logger.info(f"Sending CLEAR_CALIBRATION (Code={code})...")
        packet = build_packet(CommandCode.CLEAR_CALIBRATION, struct.pack("B", code))
        # Assuming response=False as per other control commands, but App Spec says expects_response=False
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=False)

    async def _send_set_passkey(self, passkey: bytes):
        packet = build_packet(CommandCode.SET_PASSKEY, passkey)
        await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
