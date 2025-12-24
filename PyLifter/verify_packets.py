
import pyshark
import struct
import argparse
import os

# Protocol Constants
CMD_MOVE = 0x23
CMD_GET_STATS = 0x34
CMD_GET_VERSION = 0x05
CMD_GET_NAME = 0x08
CMD_SET_NAME = 0x09
CMD_GET_PASSKEY = 0x03
CMD_SET_PASSKEY = 0x03 # Differentiated by length/direction?
CMD_CALIBRATE = 0x32
CMD_CLEAR_CALIB = 0x33

OPCODE_MAP = {
    0x00: "NACK",
    0x01: "ACK",
    0x03: "GET_PASSKEY", # or SET_PASSKEY
    0x05: "GET_PROTOCOL_VERSION",
    0x06: "CLEAR_ERROR",
    0x08: "GET_NAME",
    0x09: "SET_NAME",
    0x0A: "GET_VERSION",
    0x23: "MOVE",
    0x25: "GO_OVERRIDE",
    0x32: "CALIBRATE",
    0x33: "CLEAR_CALIBRATION", # Clear Smart Point
    0x34: "GET_STATS",
    0x40: "GET_LINK_INFO",
    0x41: "CLEAR_LINK_INFO",
    0x42: "GET_LINK_NAME",
    0x43: "SET_LINK_NAME",
    0x44: "GET_LINK_ITEM",
    0x45: "SET_LINK_item",
    # ... add others as needed
}

MOVE_CODES = {
    0: "Stop",
    1: "Up",
    2: "Down",
    3: "SmartUp",
    4: "SmartDown",
    5: "MoveRef",
    6: "StopError",
    7: "OverrideUp",
    8: "OverrideDown"
}

def parse_mylifter_packet(data):
    if len(data) < 2:
        return "Short packet"
    
    cmd = data[0]
    length = data[1]
    payload = data[2:]
    
    # Verify payload length
    if len(payload) != length:
        # Note: Sometimes captured packets might be fragmented or contain extra bytes?
        pass

    info = f"CMD: 0x{cmd:02X} Len: {length} "
    
    if cmd == CMD_MOVE:
        if len(payload) == 6:
            # Command: Code(1), Speed(1), AvgPos(4)
            move_code = payload[0]
            speed = payload[1]
            avg_pos = struct.unpack("<i", payload[2:6])[0]
            info += f"[Move CMD] Code: {MOVE_CODES.get(move_code, move_code)} ({move_code}), Speed: {speed}, AvgPos: {avg_pos}"
        elif len(payload) == 8:
             # Response: Status(1), Error(1), Pos(4), Weight(2)
             status = payload[0]
             error = payload[1]
             pos = struct.unpack("<i", payload[2:6])[0]
             weight = struct.unpack("<H", payload[6:8])[0]
             info += f"[Move RSP] Status: {status}, Error: {error}, Pos: {pos}, Weight: {weight}"
        else:
             info += f"[Move] Len={len(payload)} Raw: {payload.hex()}"


    elif cmd == CMD_GET_STATS:
         info += "[GetStats]"

    elif cmd == CMD_CALIBRATE:
         if len(payload) >= 1:
             calib_code = payload[0]
             info += f"[Calibrate] Code: {calib_code}"
    
    else:
        info += f"Payload: {payload.hex()}"
        
    return info

def analyze_pcap(pcap_path):
    print(f"Analyzing {pcap_path}...")
    try:
        cap = pyshark.FileCapture(pcap_path)
    except Exception as e:
        print(f"Error opening pcap: {e}")
        return

    count = 0
    start_time = None
    
    for packet in cap:
        # Filter for Bluetooth Attribute Protocol (ATT)
        if 'btatt' in packet:
            try:
                # Value is often in btatt.value
                val_hex = packet.btatt.value
                val_bytes = bytes.fromhex(val_hex.replace(':', ''))
                
                if start_time is None:
                    start_time = float(packet.sniff_timestamp)
                
                delta = float(packet.sniff_timestamp)                
                
                cmd = val_bytes[0]
                # Show all packets to debug flow
                parse_res = parse_mylifter_packet(val_bytes)
                print(f"[{delta:.3f}] Packet {packet.number}: {parse_res}")
                count += 1

            except AttributeError:
                continue
            except Exception as e:
                continue

    print(f"Found {count} MyLifter packets.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify MyLifter packets in pcap")
    parser.add_argument("pcap_dir", help="Directory containing pcap files")
    args = parser.parse_args()
    
    if os.path.isfile(args.pcap_dir):
        # Single file mode
        print(f"Analyzing {args.pcap_dir}...")
        analyze_pcap(args.pcap_dir)
    elif os.path.isdir(args.pcap_dir):
        # Directory mode
        for filename in os.listdir(args.pcap_dir):
            if filename.endswith(".pcap") or filename.endswith(".pcapng"):
                filepath = os.path.join(args.pcap_dir, filename)
                print(f"Analyzing {filepath}...")
                analyze_pcap(filepath)
    else:
        print(f"Error: {args.pcap_dir} is not a file or directory")
