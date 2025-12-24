import asyncio
import sys
import struct
import bleak
from datetime import datetime

# --- Logging Helper ---
def log(message):
    """Prints a message with a timestamp."""
    print(f"[{datetime.now().isoformat()}] {message}")

# This script attempts to perform the initial pairing and connection sequence to a MyLifter device.
# It uses the 'bleak' library to make a real Bluetooth connection.
#
# To install bleak:
# pip install bleak
#
# NOTE: You need to run this on a machine with a Bluetooth adapter. You may also
# need to run it with administrator/root privileges to allow BLE scanning and connection.

# --- UUIDs ---
LIFTER_SERVICE_UUID = "2d88fb13-e261-4eb9-934b-5a4fea3e3b25" # Corrected from GEMINI.md
LIFTER_WRITE_CHARACTERISTIC_UUID = "A886C7EC-31EE-48D6-9AA8-35291B21780F"
LIFTER_NOTIFY_CHARACTERISTIC_UUID = "00EFF2B2-E420-4D23-9BDD-802AF59AEB6F"

# --- Command Codes ---
SET_PASSKEY_CMD_CODE = 0x03
GET_STATS_CMD_CODE = 0x05
REAUTHENTICATION_CMD_CODE = 0x08
GET_LIFTER_STATE_CMD_CODE = 0x0a
GET_SMART_POINT_INFO_CMD_CODE = 0x40
MOVE_CMD_CODE = 0x23

# --- Move Codes ---
MOVE_CODE_STOP_ERROR = 0x06 # Used for status polling

# --- Globals ---
current_lifter_position = 0

# --- Command Builder Functions ---
def build_set_passkey_command(passkey_bytes):
    """Builds the set_passkey command payload."""
    return bytes([SET_PASSKEY_CMD_CODE]) + passkey_bytes

def build_simple_command(command_code):
    """Builds a command with a simple 0x00 payload."""
    return bytes([command_code, 0x00])

def build_reauthentication_command(payload_byte):
    """Builds the reauthentication command."""
    return bytes([REAUTHENTICATION_CMD_CODE, 0x02, payload_byte])

def build_poll_command():
    """Builds the 3-byte polling command (MOVE_STOP_ERROR)."""
    return bytes([MOVE_CMD_CODE, MOVE_CODE_STOP_ERROR, 0x00])

# --- Main Connection Logic ---
async def main_sequence(address):
    """
    The main function that connects, pairs, and then enters a stateful
    polling loop to control the MyLifter device.
    """
    passkey_received_event = asyncio.Event()
    received_passkey = [None]
    is_connected = asyncio.Event()

    def notification_handler(sender, data):
        nonlocal received_passkey
        log(f"<- NOTIFY from {sender}: {data.hex()}")
        if not data:
            return

        cmd_code = data[0]
        # Passkey notification is identified by command 0x03.
        # The actual passkey seems to be at bytes 3 and 4, based on captures.
        if cmd_code == SET_PASSKEY_CMD_CODE and len(data) > 4:
            passkey_bytes = data[3:5] # Corrected parsing based on new hypothesis
            log(f"[+] Parsed 2-byte Passkey from bytes 3-4: {passkey_bytes.hex()}")
            received_passkey[0] = passkey_bytes
            passkey_received_event.set()
        elif cmd_code == SET_PASSKEY_CMD_CODE and len(data) > 2:
            # Fallback for the shorter notification format seen in app capture
            passkey_bytes = data[1:3]
            log(f"[+] Parsed 2-byte Passkey from bytes 1-2: {passkey_bytes.hex()}")
            received_passkey[0] = passkey_bytes
            passkey_received_event.set()


    def disconnected_callback(client):
        log(f"[!!!] Device disconnected unexpectedly!")
        is_connected.clear()

    log(f"--- Starting MyLifter Sequence for {address} ---")
    try:
        async with bleak.BleakClient(address, disconnected_callback=disconnected_callback) as client:
            if not client.is_connected:
                log(f"[!] Failed to connect to {address}")
                return
            
            is_connected.set()
            log(f"[+] Connected to {address}")

            # --- 1. Start Notifications ---
            log("\n[1] Starting notifications...")
            await client.start_notify(LIFTER_NOTIFY_CHARACTERISTIC_UUID, notification_handler)
            log("[+] Notifications started.")
            await asyncio.sleep(0.2)

            # --- 2. First Handshake (Passkey Exchange) ---
            log("\n--- Performing First Handshake ---")
            log("[2] Priming passkey request...")
            # Use write-without-response as seen in packet capture
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_simple_command(SET_PASSKEY_CMD_CODE), response=False)
            
            log("\n[3] WAITING FOR PASSKEY... Press the 'Pair' button on your MyLifter device.")
            try:
                await asyncio.wait_for(passkey_received_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                log("[!] Timed out waiting for passkey. Aborting.")
                return

            if received_passkey[0] is None:
                log("[!] Failed to parse passkey from notification. Aborting.")
                return

            log(f"\n[4] Sending SET_PASSKEY command with received key {received_passkey[0].hex()}...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_set_passkey_command(received_passkey[0]), response=False)
            await asyncio.sleep(0.1)

            # --- 3. Second Handshake (Re-authentication) ---
            log("\n--- Performing Second Handshake (Re-authentication) ---")
            log("[5] Sending re-authentication command 1 (0x080200)...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_reauthentication_command(0x00), response=False)
            await asyncio.sleep(0.1) # Delay between re-auth commands

            log("[6] Sending re-authentication command 2 (0x080210)...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_reauthentication_command(0x10), response=False)
            await asyncio.sleep(0.1)

            # --- 4. Info-Gathering ---
            log("\n--- Performing Info-Gathering ---")
            log("[7] Sending GET_STATS command...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_simple_command(GET_STATS_CMD_CODE), response=False)
            await asyncio.sleep(0.1)

            log("[8] Sending GET_LIFTER_STATE command...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_simple_command(GET_LIFTER_STATE_CMD_CODE), response=False)
            await asyncio.sleep(0.1)

            log("[9] Sending GET_SMART_POINT_INFO command...")
            await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_simple_command(GET_SMART_POINT_INFO_CMD_CODE), response=False)
            await asyncio.sleep(0.5) # Wait for commands to be processed before polling

            # --- 5. Limited Polling Loop ---
            log("\n--- Handshake Complete. Entering 10-Second Polling Loop. ---")
            
            polling_duration = 10  # seconds
            polling_interval = 0.2  # 200ms interval from captures
            num_iterations = int(polling_duration / polling_interval)

            for i in range(num_iterations):
                if not is_connected.is_set():
                    log("[!] Device disconnected during polling loop.")
                    break
                log(f"Polling iteration {i+1}/{num_iterations}")
                await client.write_gatt_char(LIFTER_WRITE_CHARACTERISTIC_UUID, build_poll_command(), response=False)
                await asyncio.sleep(polling_interval)
            
            log("\n[*] Polling loop finished.")

    except asyncio.TimeoutError:
        log("\n[!] A general timeout occurred during the sequence.")
    except bleak.exc.BleakError as e:
        log(f"\n[!] A Bluetooth error occurred: {e}")
    except Exception as e:
        log(f"\n[!] An unexpected error occurred: {e}")
    finally:
        log("[*] Disconnecting...")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python testconnection.py <mac_address>")
        print("Example: python testconnection.py 00:11:22:33:44:55")
        sys.exit(1)

    mac_address = sys.argv[1]
    try:
        asyncio.run(main_sequence(mac_address))
    except KeyboardInterrupt:
        log("\n[*] Script stopped by user.")