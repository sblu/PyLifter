
from pylifter.protocol import *

stop_pkt = build_move_packet(MoveCode.STOP, speed=0, avg_pos=0)
print(f"STOP Packet: {stop_pkt.hex()}")
expected = "2306000000000000"
print(f"Matches Expected? {stop_pkt.hex() == expected}")

move_pkt = build_move_packet(MoveCode.UP, speed=100, avg_pos=0)
print(f"MOVE Packet: {move_pkt.hex()}")
expected_move = "2306016400000000"
print(f"Matches Expected? {move_pkt.hex() == expected_move}")
