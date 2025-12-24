
import asyncio
import argparse
import logging
from pylifter.client import PyLifterClient, MoveCode

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("calibrate")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", required=True, help="MAC address of the MyLifter")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration to move (seconds)")
    parser.add_argument("--direction", default="UP", choices=["UP", "DOWN"], help="Direction to move")
    parser.add_argument("--speed", type=int, default=100, help="Speed (0-100)")
    args = parser.parse_args()
    
    direction = MoveCode.UP if args.direction == "UP" else MoveCode.DOWN
    PASSKEY = "2378a8dbc69c"
    
    client = PyLifterClient(args.mac, PASSKEY)
    
    try:
        await client.connect()
        logger.info("Connected.")
        
        # Wait for position update
        logger.info("Waiting for initial position reading...")
        start_pos = -1
        for _ in range(20):
            if client._last_known_position != 0: # Wait for non-zero or at least settled? Actually 0 is valid.
                pass 
            start_pos = client._last_known_position
            await asyncio.sleep(0.1)
            
        # Refetch just to be sure
        start_pos = client._last_known_position
        print("\n" + "="*50)
        print(f"STARTING POSITION: {start_pos}")
        print("Please measure the height of the winch hook now (e.g. from floor).")
        print("Prepare to measure AGAIN after movement.")
        print("="*50 + "\n")
        
        await asyncio.sleep(4.0) # Give user time to see this if running manually, though run_command hides it.
        # Actually run_command hides output until done.
        # We will assume user runs this, or we just output the data for them to correlate.
        
        logger.info(f"Moving {args.direction} for {args.duration} seconds...")
        await client.move(direction, speed=args.speed)
        
        # Wait duration
        steps = int(args.duration * 10)
        for i in range(steps):
             await asyncio.sleep(0.1)
             if i % 10 == 0:
                 print(f"Moving... {client._last_known_position}")
                 
        await client.stop()
        logger.info("Stopped.")
        
        # Wait for final position to settle
        await asyncio.sleep(2.0)
        end_pos = client._last_known_position
        
        delta = end_pos - start_pos
        
        print("\n" + "="*50)
        print(f"FINISHED.")
        print(f"Start Position: {start_pos}")
        print(f"End Position:   {end_pos}")
        print(f"Delta (Ticks):  {delta}")
        print("="*50)
        print("Please measure the new height.")
        print("Calculate Ticks Per Inch:  abs(Delta) / abs(NewHeight - OldHeight)")
        print("="*50 + "\n")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
