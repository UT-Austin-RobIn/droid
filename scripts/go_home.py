#!/usr/bin/env python3
"""Move the Franka to the standard home pose and open the gripper.

    python scripts/go_home.py

Attaches to the already-running NUC controller (does not launch one).
Same joints as teleop episode start.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RESET_JOINTS = np.array([0, -np.pi / 5, 0, -4 * np.pi / 5, 0, 3 * np.pi / 5, 0.0])


def main() -> int:
    from droid.misc.parameters import nuc_ip
    from droid.misc.server_interface import ServerInterface

    print("connecting to {} -- the arm will move to home".format(nuc_ip))
    robot = ServerInterface(ip_address=nuc_ip, launch=False)
    robot.get_robot_state()

    robot.update_gripper(0, velocity=False, blocking=True)
    robot.update_joints(RESET_JOINTS, velocity=False, blocking=True)

    print("home")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
