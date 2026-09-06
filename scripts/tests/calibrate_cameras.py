from droid.controllers.oculus_controller import VRPolicy
from droid.robot_env import RobotEnv
from droid.trajectory_utils.misc import calibrate_camera

# Make the robot env
env = RobotEnv()
controller = VRPolicy()
camera_id = "39406856"  # left camera (varied_camera_1)

print("Ready")
calibrate_camera(env, camera_id, controller)
