from cable_robot_demo import CableRobot
import math

config = {
    "dimensions": {"width_cm": 400.0, "length_cm": 400.0, "height_cm": 300.0},
    "safety": {"min_floor_margin_cm": 20.0, "min_ceiling_margin_cm": 50.0, "safe_angle_deg": 60.0}
}

robot = CableRobot(config)

print("--- Testing Safety ---")
# Safe Center
print(f"Center (200, 200, 150): {robot.is_safe(200, 200, 150)}")
# Unsafe High
print(f"Too High (200, 200, 280): {robot.is_safe(200, 200, 280)}")
# Unsafe Angle (Corner)
print(f"Corner (10, 10, 150): {robot.is_safe(10, 10, 150)}")

print("\n--- Testing IK ---")
# Center
lens = robot.inverse_kinematics(200, 200, 150)
print(f"Center Lengths: {lens}")
# Expected: Sqrt(200^2 + 200^2 + (300-150)^2) = Sqrt(40000+40000+22500) = Sqrt(102500) approx 320.15
expected = math.sqrt(200**2 + 200**2 + 150**2)
print(f"Expected: {expected:.2f}")

