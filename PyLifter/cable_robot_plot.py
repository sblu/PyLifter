import matplotlib.pyplot as plt
import numpy as np
import logging
import argparse
import json
import os
import sys

# Import CableRobot class. 
# We assume this script is in the same directory as cable_robot_demo.py
try:
    from cable_robot_demo import CableRobot
except ImportError:
    # Fallback or error handling if run from wrong dir
    print("Error imports: Ensure cable_robot_demo.py is in the Python path.")
    pass

def create_robot_plot(robot, current_pos_xyz=None, title_suffix=""):
    """
    Creates a 3D plot of the cable robot.
    Returns the figure object.
    """
    print("Generating 3D Visualization... (this may take a moment)")
    
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    
    # 1. Draw Bounding Box
    w, l, h = robot.width, robot.length, robot.height
    
    # Vertices
    corners = np.array([
        [0, 0, 0], [w, 0, 0], [w, l, 0], [0, l, 0],  # Floor
        [0, 0, h], [w, 0, h], [w, l, h], [0, l, h]   # Ceiling
    ])
    
    # Edges
    edges = [
        [0,1], [1,2], [2,3], [3,0], # Floor loop
        [4,5], [5,6], [6,7], [7,4], # Ceiling loop
        [0,4], [1,5], [2,6], [3,7]  # Pillars
    ]
    
    for e in edges:
        p1 = corners[e[0]]
        p2 = corners[e[1]]
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], [p1[2], p2[2]], 'k-', lw=1, alpha=0.3)
        
    # 2. Draw Anchors
    ax.scatter(
        [a[0] for a in robot.anchors.values()],
        [a[1] for a in robot.anchors.values()],
        [a[2] for a in robot.anchors.values()],
        c='red', marker='v', s=100, label='Winches'
    )
    
    # 3. Generate Safe Cloud
    # Optimization: If W/L/H are large, scale step to limit points
    max_dim = max(w, l, h)
    step = max_dim / 40.0 # (~40 steps)
    
    safe_x = []
    safe_y = []
    safe_z = []
    
    for x in np.arange(0, w + 1, step):
        for y in np.arange(0, l + 1, step):
            for z in np.arange(0, h + 1, step):
                # Basic bounds check first for speed
                if not (0 <= x <= w and 0 <= y <= l and robot.min_floor_margin <= z <= (robot.height - robot.min_ceiling_margin)):
                    continue
                
                safe, _ = robot.is_safe(x, y, z)
                if safe:
                    safe_x.append(x)
                    safe_y.append(y)
                    safe_z.append(z)
                    
    ax.scatter(safe_x, safe_y, safe_z, c='lime', alpha=0.2, s=10, marker='.', label='Safe Zone')
    
    # 4. Draw Current Position
    if current_pos_xyz is not None:
        cx, cy, cz = current_pos_xyz
        ax.scatter([cx], [cy], [cz], c='blue', marker='o', s=100, label='Payload')
        
        # Draw Cables
        for wid, anchor in robot.anchors.items():
            ax.plot([anchor[0], cx], [anchor[1], cy], [anchor[2], cz], 'b--', lw=2)
            
    ax.set_xlabel('X (Width)')
    ax.set_ylabel('Y (Length)')
    ax.set_zlabel('Z (Height)')
    ax.set_title(f"Cable Robot Workspace {title_suffix}")
    ax.legend()
    
    # Aspect Ratio
    max_range = np.array([w, l, h]).max() / 2.0
    mid_x = w * 0.5
    mid_y = l * 0.5
    mid_z = h * 0.5
    ax.set_xlim(mid_x - max_range, mid_x + max_range)
    ax.set_ylim(mid_y - max_range, mid_y + max_range)
    ax.set_zlim(mid_z - max_range, mid_z + max_range)

    return fig

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to pylifter_config.json")
    parser.add_argument("--pos", required=True, help="Current position 'x,y,z'")
    
    args = parser.parse_args()
    
    # Load Config
    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)
        
    with open(args.config, 'r') as f:
        full_config = json.load(f)
        
    robot_config = full_config.get("cable_robot", {})
    
    # Instantiate Robot (logic class only)
    # We do NOT initialize winches (connections) here, just geometry.
    robot = CableRobot(robot_config, sim_mode=True) 
    
    # Parse Position
    try:
        px, py, pz = map(float, args.pos.split(','))
        pos = (px, py, pz)
    except Exception as e:
        print(f"Invalid position format: {e}")
        pos = None
        
    create_robot_plot(robot, pos, title_suffix="(Visualizer)")
    
    print("Plot opened. Creating interactive window...")
    plt.show() # Blocking call for this process
