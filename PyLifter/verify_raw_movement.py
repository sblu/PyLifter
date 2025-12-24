
import asyncio
from bleak import BleakClient, BleakScanner

# Constants
MAC_ADDRESS = "CC:CC:CC:FE:15:33"
MYLIFTER_SERVICE_UUID = "2d88fb13-e261-4eb9-934b-5a4fea3e3b25"
COMMAND_CHAR_UUID = "A886C7EC-31EE-48D6-9AA8-35291B21780F"
RESPONSE_CHAR_UUID = "00EFF2B2-E420-4D23-9BDD-802AF59AEB6F"

PASSKEY_PACKET = bytes.fromhex("03062378a8dbc69c") # Set Passkey
STOP_PACKET = bytes.fromhex("2306000000000000")    # Move Code 0 (Stop), Speed 0, Pos 0
MOVE_UP_PACKET = bytes.fromhex("2306016400000000") # Move Code 1 (Up), Speed 100 (0x64), Pos 0

async def notification_handler(sender, data):
    print(f"RX: {data.hex()}")

async def main():
    print(f"Connecting to {MAC_ADDRESS}...")
    async with BleakClient(MAC_ADDRESS) as client:
        print("Connected.")
        
        await client.start_notify(RESPONSE_CHAR_UUID, notification_handler)
        print("Notifications enabled.")
        
        # 1. Send Passkey
        print("Sending Passkey...")
        await client.write_gatt_char(COMMAND_CHAR_UUID, PASSKEY_PACKET, response=False)
        
        # 2. Immediate Keep-Alive Loop (Idle)
        print("Entering Idle Loop (2 seconds)...")
        for _ in range(20): # 20 * 0.1s = 2s
            await client.write_gatt_char(COMMAND_CHAR_UUID, STOP_PACKET, response=False)
            await asyncio.sleep(0.1)
            
        # 3. Move UP
        print("Sending MOVE UP (5 seconds)...")
        for i in range(50): # 50 * 0.1s = 5s
            await client.write_gatt_char(COMMAND_CHAR_UUID, MOVE_UP_PACKET, response=False)
            await asyncio.sleep(0.1)
            
        # 4. Stop
        print("Stopping...")
        for _ in range(10):
            await client.write_gatt_char(COMMAND_CHAR_UUID, STOP_PACKET, response=False)
            await asyncio.sleep(0.1)
            
        print("Done. Disconnecting...")

if __name__ == "__main__":
    asyncio.run(main())
