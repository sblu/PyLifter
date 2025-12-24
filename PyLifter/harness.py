
import asyncio
import argparse
import subprocess
import signal
import sys
from bleak import BleakScanner, BleakClient
from pylifter.protocol import *

# Global cleanup for manual tshark handling
tshark_proc = None

# Event to signal auth completion
auth_event = asyncio.Event()
# Event for command completion
cmd_event = asyncio.Event()

async def scan():
    print("Scanning for MyLifter devices...")
    devices = await BleakScanner.discover(service_uuids=[MYLIFTER_SERVICE_UUID])
    if not devices:
        print("No MyLifter devices found.")
        return []
    
    print(f"Found {len(devices)} devices:")
    for d in devices:
        print(f"  {d.name} ({d.address})")
    return devices

async def run_harness(mac_address, command, speed, distance, capture_file):
    global tshark_proc
    
    if capture_file:
        print(f"Starting tshark capture to {capture_file}...")
        # Start tshark in background
        cmd = ["sudo", "tshark", "-i", "bluetooth0", "-w", capture_file]
        try:
            tshark_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(2) # Wait for tshark to start
        except Exception as e:
            print(f"Failed to start tshark: {e}")
            tshark_proc = None

    print(f"Connecting to {mac_address}...")
    async with BleakClient(mac_address) as client:
        print("Connected.")
        
        # Subscribe to notifications
        def notification_handler(sender, data):
            print(f"RX: {data.hex()}")
            
            # Simple parser based on first byte
            cmd = data[0] if len(data) > 0 else None
            
            if cmd == CommandCode.GET_PASSKEY:
                # Payload begins at index 2 (Cmd, Len, Payload...)
                if len(data) >= 8: # 1+1+6
                    passkey = data[2:8]
                    print(f"Received Passkey: {passkey.hex()}")
                    # Send Set Passkey
                    asyncio.create_task(authenticate(client, passkey))
            
            elif cmd == CommandCode.ACK:
                # Payload is the command code being Acked
                if len(data) >= 3:
                     acked_cmd = data[2]
                     print(f"ACK Received for Cmd: {acked_cmd:#04x}")
                     if acked_cmd == CommandCode.SET_PASSKEY:
                         print("Authentication confirmed (ACK received).")
                         auth_event.set()

            elif cmd == CommandCode.SET_PASSKEY:
                # Should not happen because SET_PASSKEY=0x03 covered by GET_PASSKEY check if logic flawn?
                # Actually GET_PASSKEY and SET_PASSKEY are same int value (3).
                # But here we only expect GET_PASSKEY as a notification from device (providing the key).
                # SET_PASSKEY is what WE send. The device responds with ACK.
                pass


            elif cmd == CommandCode.MOVE:
                payload = data[2:]
                try:
                    parsed = parse_move_response(payload)
                    print(f"Move Status: {parsed}")
                except Exception as e:
                    print(f"Error parsing move response: {e}")

            elif cmd == CommandCode.GET_STATS:
                print(f"Stats Received: {data.hex()}")
                cmd_event.set()
                
            elif cmd == CommandCode.GET_VERSION:
                print(f"Version Received: {data.hex()}")

        await client.start_notify(RESPONSE_CHAR_UUID, notification_handler)
        print("Subscribed. Starting Authentication Handshake...")

        # 1. Send Get Passkey
        print("Sending Get Passkey...")
        packet = build_packet(CommandCode.GET_PASSKEY)
        await client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
        
        # Wait for Auth
        try:
            await asyncio.wait_for(auth_event.wait(), timeout=5.0)
            print("Authenticated!")
        except asyncio.TimeoutError:
            print("Authentication Timed Out! Device might not be responding.")
            return

        # Execute Command
        if command == "move_up":
            print(f"Moving UP at speed {speed}...")
            packet = build_move_packet(MoveCode.UP, speed=speed)
            await client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
            # Run for some time then stop
            await asyncio.sleep(2)
            print("Stopping...")
            stop_packet = build_move_packet(MoveCode.STOP)
            await client.write_gatt_char(COMMAND_CHAR_UUID, stop_packet, response=True)

        elif command == "move_down":
            print(f"Moving DOWN at speed {speed}...")
            packet = build_move_packet(MoveCode.DOWN, speed=speed)
            await client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
            await asyncio.sleep(2)
            print("Stopping...")
            stop_packet = build_move_packet(MoveCode.STOP)
            await client.write_gatt_char(COMMAND_CHAR_UUID, stop_packet, response=True)
        
        elif command == "get_stats":
            print("Getting Stats...")
            packet = build_packet(CommandCode.GET_STATS)
            await client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)
            try:
                await asyncio.wait_for(cmd_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                print("Stats timeout")

        await asyncio.sleep(1)
        print("Disconnecting...")

async def authenticate(client, passkey):
    print(f"Sending Set Passkey: {passkey.hex()}...")
    # build_packet puts Cmd, Len, Payload.
    # Payload for SetPasskey is just the 6 bytes.
    packet = build_packet(CommandCode.SET_PASSKEY, passkey)
    await client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyLifter Test Harness")
    parser.add_argument("mode", choices=["scan", "run"], help="Mode: scan or run")
    parser.add_argument("--mac", help="MAC address of device (required for run)")
    parser.add_argument("--cmd", choices=["move_up", "move_down", "get_stats"], default="get_stats")
    parser.add_argument("--speed", type=int, default=100)
    parser.add_argument("--capture", help="Path to save pcap file")

    args = parser.parse_args()

    # loop = asyncio.get_event_loop() # Deprecated
    
    if args.mode == "scan":
        asyncio.run(scan())
    elif args.mode == "run":
        if not args.mac:
            print("Error: --mac required for run mode")
            sys.exit(1)
        asyncio.run(run_harness(args.mac, args.cmd, args.speed, None, args.capture))
