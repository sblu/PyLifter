import asyncio
import argparse
import struct
from pylifter.client import PyLifterClient, MoveCode

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", required=True, help="MAC address of the MyLifter")
    args = parser.parse_args()
    
    # 1. Connect
    print(f"Connecting to {args.mac}...")
    client = PyLifterClient(args.mac, passkey="2378a8dbc69c")
    
    try:
        await client.connect()
        print("Connected and Authenticated.")
        
        # 2. Minimal Wait for stability
        print("Waiting 2s...")
        await asyncio.sleep(2.0)
        
        # 3. Move Up
        print("Starting Move UP...")
        await client.move(MoveCode.UP, speed=100)
        
        # Run for 5 seconds
        for i in range(5):
            print(f"Moving... {i+1}")
            await asyncio.sleep(1.0)
            
        # 4. Stop
        print("Stopping...")
        await client.stop()
        await asyncio.sleep(1.0)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")
    finally:
        print("Disconnecting...")
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
