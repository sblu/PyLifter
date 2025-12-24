
import struct
from enum import IntEnum

# UUIDs
MYLIFTER_SERVICE_UUID = "2d88fb13-e261-4eb9-934b-5a4fea3e3b25"
COMMAND_CHAR_UUID = "A886C7EC-31EE-48D6-9AA8-35291B21780F"
RESPONSE_CHAR_UUID = "00EFF2B2-E420-4D23-9BDD-802AF59AEB6F"

class CommandCode(IntEnum):
    NACK = 0x00
    ACK = 0x01
    GET_PASSKEY = 0x03
    SET_PASSKEY = 0x03
    GET_PROTOCOL_VERSION = 0x05
    CLEAR_ERROR = 0x06
    GET_NAME = 0x08
    SET_NAME = 0x09
    GET_VERSION = 0x0A
    MOVE = 0x23
    CALIBRATE = 0x32
    CLEAR_CALIBRATION = 0x33
    GET_STATS = 0x34
    GET_LINK_INFO = 0x40
    CLEAR_LINK_INFO = 0x41
    GET_LINK_NAME = 0x42
    SET_LINK_NAME = 0x43
    GET_LINK_ITEM = 0x44
    SET_LINK_ITEM = 0x45
    FIRMWARE_FILE_START = 0x50
    FIRMWARE_FILE_BLOCK_START = 0x51
    FIRMWARE_FILE_BLOCK_DATA = 0x52
    FIRMWARE_FILE_VALIDATE_CHECK = 0x53
    FIRMWARE_FILE_FINALIZE = 0x54
    FIRMWARE_FILE_ABORT = 0x55
    FACTORY_CALIBRATE = 0xFA

class MoveCode(IntEnum):
    STOP = 0
    UP = 1
    DOWN = 2
    SMART_UP = 3
    SMART_DOWN = 4
    MOVE_REFERENCE = 5
    STOP_ERROR = 6
    OVERRIDE_UP = 7
    OVERRIDE_DOWN = 8

class SmartPointCode(IntEnum):
    REFERENCE = 0
    TOP = 1
    BOTTOM = 2

def build_packet(command_code: int, payload: bytes = b'') -> bytes:
    """
    Constructs a MyLifter Bluetooth packet.
    Format: [Command Code (1B)][Payload Length (1B)][Payload]
    """
    cmd_byte = struct.pack("B", command_code)
    len_byte = struct.pack("B", len(payload))
    return cmd_byte + len_byte + payload

def build_move_packet(move_code: MoveCode, speed: int = 100, avg_pos: int = 0) -> bytes:
    """
    Constructs a Move command packet.
    Payload: [Move Code (1B)][Speed (1B)][Avg Pos (4B, Little Endian)]
    """
    payload = struct.pack("<BBi", move_code, speed, avg_pos)
    return build_packet(CommandCode.MOVE, payload)

def build_set_smart_point_packet(point: SmartPointCode) -> bytes:
    """
    Constructs a Calibrate (Set Smart Point) command packet.
    Payload: [Smart Point Code (1B)]
    """
    payload = struct.pack("B", point)
    return build_packet(CommandCode.CALIBRATE, payload)

def build_clear_smart_point_packet(point: SmartPointCode) -> bytes:
    """
    Constructs a Clear Calibration command packet.
    Payload: [Smart Point Code (1B)]
    """
    payload = struct.pack("B", point)
    return build_packet(CommandCode.CLEAR_CALIBRATION, payload)

def parse_move_response(payload: bytes) -> dict:
    """
    Parses the response to a Move command.
    Payload: [Status (1B)][Error (1B)][Position (4B)][Weight (2B)]
    WARNING: This assumes the input `payload` does NOT include the Command and Length bytes.
    Note: The actual response packet might include a redundant length byte at start of payload?
    Capture analysis suggests response payload is 8 bytes.
    If the wrapper strips Cmd/Len, we get 8 bytes.
    """
    if len(payload) != 8:
        raise ValueError(f"Invalid move response length: {len(payload)}, expected 8")
    
    move_status, error_code, position, weight = struct.unpack("<BBih", payload)
    
    return {
        "move_status": move_status,
        "error_code": error_code,
        "position": position,
        "weight": weight
    }

