
import asyncio
import logging
import argparse
from pylifter.client import PyLifterClient
from pylifter.protocol import MoveCode

logging.basicConfig(level=logging.DEBUG)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", required=True)
    args = parser.parse_args()

    # Passkey from logs: 2378a8dbc69c
    client = PyLifterClient(args.mac, passkey="2378a8dbc69c")
    try:
        await client.connect()
        
        # TAKE 27: Factory Calibrate to reset persistent error
        print("Attempting FACTORY_CALIBRATE (Code 1)...")
        # Ensure we are listing to disconnect events if possible, but for now just try/except
        try:
            await client.factory_calibrate(1)
            print("Factory Calibrate sent. Waiting 20s for calibration sequence and reboot...")
            await asyncio.sleep(20.0)
        except Exception as e:
            print(f"Disconnect expected during calibration: {e}")

    except Exception as e:
        print(f"Initial connection error: {e}")
    finally:
        await client.disconnect()
        
    print("Reconnecting to verify fix...")
    await asyncio.sleep(5.0)
    
    client = PyLifterClient(args.mac, passkey="2378a8dbc69c")
    try:
        await client.connect()
        print("Reconnected!")
        
        print("Checking Stats (Expect 0 errors)...")
        await client.get_stats()
        
        print("Attempting Move Up (Speed 100)...")
        await client.move(MoveCode.UP, speed=100)
        await asyncio.sleep(3.0)
        
        print("Stopping...")
        await client.stop()
        await asyncio.sleep(1.0)
        
    except Exception as e:
        print(f"Reconnection/Move failed: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
