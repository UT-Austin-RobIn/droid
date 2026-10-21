# ruff: noqa

import atexit
import contextlib
import dataclasses
import datetime
import faulthandler
import math
import os
import select
import signal
import sys
import threading
import time
from moviepy.editor import ImageSequenceClip
import numpy as np
from openpi_client import image_tools
from openpi_client import websocket_client_policy
import pandas as pd
from PIL import Image
from droid.robot_env import RobotEnv
import tqdm
import tyro
from typing import Literal, Optional, List
from pathlib import Path
faulthandler.enable()

# # == failure_data project specific ==
from oopsie_data_tools.annotation_tool.annotation_schema import OUTCOME_SUCCESS
from oopsie_data_tools.utils.validation.episode_validator import MIN_EPISODE_DURATION_S
from oopsie_data_tools.annotation_tool.episode_recorder import EpisodeRecorder
from oopsie_data_tools.utils.robot_profile.robot_profile import *

# DROID data collection frequency -- we slow down execution to match this frequency
DROID_CONTROL_FREQUENCY = 15


@dataclasses.dataclass
class Args:
    # Hardware parameters
    left_camera_id: str = "39406856"  # e.g., "24259877"
    right_camera_id: str = "36088355"  # e.g., "24514023"
    wrist_camera_id: str = "17651206"  # e.g., "13062452"

    # Policy parameters
    external_camera: Optional[str] = None

    # Rollout parameters
    max_timesteps: int = 1000
    # How many actions to execute from a predicted action chunk before querying policy server again
    # 8 is usually a good default (equals 0.5 seconds of action execution).
    open_loop_horizon: int = 8

    # Remote server parameters
    remote_host: str = "0.0.0.0"  # point this to the IP address of the policy server, e.g., "192.168.1.100"
    remote_port: int = (
        8000  # point this to the port of the policy server, default server port for openpi servers is 8000
    )

    # == OopsieData project specific ==
    data_root_dir: Path = Path("./data")  # annotation tool reads MP4s and JSON from here    
    resume_session_name: str = None
    annotator_port: int = 5003
    wait_for_annotation: bool = True  # block until browser annotation is submitted

    operator_name: str = "myoungkyu"
    annotator_name: str = "myoungkyu"

    # == VR (Oculus) control ==
    # Pass --vr true to drive start/stop/labelling from the Quest controller instead of
    # the keyboard. A plain bool would spell the values True/False, so this is a string.
    vr: Literal["true", "false"] = "false"
    # How long A, B or A+B has to stay down to count as a hold rather than a tap.
    vr_hold_secs: float = 2.0
    
    # robot_profile: str = "configs/robot_profiles/openpi_example_robot_profile.yaml"

# We are using Ctrl+C to optionally terminate rollouts early -- however, if we press Ctrl+C while the policy server is
# waiting for a new action chunk, it will raise an exception and the server connection dies.
# This context manager temporarily prevents Ctrl+C and delays it after the server call is complete.
@contextlib.contextmanager
def prevent_keyboard_interrupt():
    """Temporarily prevent keyboard interrupts by delaying them until after the protected code."""
    interrupted = False
    original_handler = signal.getsignal(signal.SIGINT)

    def handler(signum, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original_handler)
        if interrupted:
            raise KeyboardInterrupt


# == Rollout labelling ==
# The four outcome slugs plus "discard", which is not an outcome: it means the episode
# is never written at all. Nothing reaches disk until _save_episode runs, so discarding
# is just skipping it -- there are no partial files to clean up.
KEYBOARD_LABEL_CHOICES = {
    "1": "success",
    "2": "success_suboptimal",
    "3": "success_side_effect",
    "4": "failure",
    "5": "discard",
}


def _keyboard_label_outcome() -> str:
    """Prompt for a label on the terminal. Returns an outcome slug or "discard".

    Ctrl+C is swallowed and re-prompts rather than propagating. The rollout is still only
    in memory at this point -- the videos and the h5 are not written until the label is
    known -- so letting KeyboardInterrupt out of here would throw the whole episode away.
    Ctrl+C is also how the rollout itself is stopped, so a second press landing on this
    prompt is easy to do by accident. Type 5 to discard on purpose.
    """
    while True:
        try:
            choice = input(
                "Label (1-5): (1) success, (2) success suboptimal, "
                "(3) success side effect, (4) failure, (5) discard: "
            ).strip()
        except KeyboardInterrupt:
            print("\n  Ctrl+C ignored -- this episode is NOT saved yet. "
                  "Label it (5 discards it).")
            continue
        if choice in KEYBOARD_LABEL_CHOICES:
            return KEYBOARD_LABEL_CHOICES[choice]
        print(f"Enter 1-5, but got: {choice!r}")


def _save_episode(episode_recorder, instruction: str, outcome: str, annotator_name: str) -> None:
    """Write the episode out, then patch the outcome slug into it.

    finish_rollout only accepts a numeric success, which cannot distinguish the two
    qualified successes from a clean one. So the slug goes in on a second pass, through
    the same annotation helpers the web annotation tool writes with.
    """
    episode_recorder.finish_rollout(instruction=instruction, success=OUTCOME_SUCCESS[outcome])
    h5_path = episode_recorder.session_dir / f"{episode_recorder.save_fname}.h5"
    EpisodeRecorder.patch_h5_failure_annotation(
        h5_path,
        {
            "annotator": annotator_name,
            "outcome": outcome,
            "timestamp": datetime.datetime.now().timestamp(),
        },
    )
    print(f"  saved {h5_path} as {outcome}")


# == VR (Oculus) start/stop control -- only reached when --vr true ==
# The controller gives us exactly two buttons (A and B, via VRPolicy.get_info()), and
# nothing is ever drawn in the headset: every prompt below prints to the terminal. The
# headset is only a button source, so you can run with it on your head and read the
# terminal, or keep it off and use Enter where noted.


def _build_vr_controller():
    """Start the Oculus reader.

    Imported inside the function on purpose: oculus_reader is imported at module scope
    by droid.controllers.oculus_controller and talks to a headset over adb, so running
    without --vr true must never reach it.
    """
    from droid.controllers.oculus_controller import VRPolicy

    return VRPolicy()


def _wait_buttons_up(controller, poll_s: float = 0.05) -> None:
    """Block until A and B are both released.

    The press that ended the previous phase is usually still down. Without this wait it
    would be read straight away as the answer to the next prompt.
    """
    nudged = False
    while True:
        info = controller.get_info()
        if not info["success"] and not info["failure"]:
            return
        if not nudged:
            nudged = True
            print("  (release A/B first...)")
        time.sleep(poll_s)


class _VRStopLatch:
    """Remember an A or B press that lands while the policy server call is blocking.

    get_info() is only read at the top of each step, and policy_client.infer() can block
    for a couple of seconds -- a tap that goes down and back up inside that window is
    gone by the time the loop looks again. This samples at 50Hz on its own thread
    instead, and the flag is sticky until stop().
    """

    def __init__(self, controller, poll_s: float = 0.02) -> None:
        self._controller = controller
        self._poll_s = poll_s
        self._pressed: Optional[str] = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, name="vr-stop-latch", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            try:
                info = self._controller.get_info()
            except Exception:  # a dropped headset read is not a reason to stop watching
                continue
            if info["success"]:
                self._pressed = "A"
            elif info["failure"]:
                self._pressed = "B"

    @property
    def pressed(self) -> Optional[str]:
        """"A" or "B" once either has been seen, else None."""
        return self._pressed

    def stop(self) -> None:
        self._stop.set()


# Stop the rollout and keep the episode. Distinct from Ctrl+C only in intent: Ctrl+C
# also keeps the episode, but it reads as "kill" and a second press used to be fatal.
STOP_KEY = "a"


_SAVED_TTY = None


def _enable_single_keypresses() -> bool:
    """Put stdin in cbreak so one keypress is readable without Enter.

    Returns False and changes nothing when there is no tty. cbreak leaves ISIG enabled,
    so Ctrl+C still raises KeyboardInterrupt as before. An atexit restore is registered
    so even a crash cannot leave the terminal in cbreak.
    """
    global _SAVED_TTY
    try:
        import termios
        import tty

        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        _SAVED_TTY = (fd, termios.tcgetattr(fd))
        tty.setcbreak(fd)
    except Exception:  # no tty, no termios -- the keyboard stop key is simply unavailable
        _SAVED_TTY = None
        return False
    atexit.register(_restore_keypresses)
    return True


def _restore_keypresses() -> None:
    """Undo _enable_single_keypresses. Safe to call when it was never enabled."""
    global _SAVED_TTY
    if _SAVED_TTY is None:
        return
    fd, saved = _SAVED_TTY
    _SAVED_TTY = None
    try:
        import termios

        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    except Exception:
        pass


def _poll_keypress() -> Optional[str]:
    """Return one waiting keypress lowercased, or None. Only valid inside
    _single_keypresses()."""
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    return ch.lower() if ch else None


def _stdin_label() -> Optional[str]:
    """Non-blocking read of a typed 1-5 label, as a fallback for the VR prompt.

    If the headset is asleep or out of battery, get_info() reports nothing pressed and
    the label prompt would otherwise block forever with the finished episode still only
    in memory -- where Ctrl+C loses it. The keyboard is always available.
    """
    try:
        if not sys.stdin.isatty():
            return None
    except Exception:
        return None
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    return KEYBOARD_LABEL_CHOICES.get(sys.stdin.readline().strip())


def _enter_pressed() -> bool:
    """True if a line is waiting on stdin, so Enter can stand in for A."""
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    if select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
        return True
    return False


def _vr_wait_for_a_or_b(controller, prompt: str, *, allow_enter: bool = False, poll_s: float = 0.05) -> bool:
    """Print *prompt*, then block until A (returns True) or B (returns False)."""
    print(prompt)
    _wait_buttons_up(controller, poll_s=poll_s)
    while True:
        info = controller.get_info()
        if info["success"]:
            return True
        if info["failure"]:
            return False
        if allow_enter and _enter_pressed():
            return True
        time.sleep(poll_s)


def _vr_wait_to_start(controller) -> bool:
    """Gate before the arm moves. A (or Enter) runs the rollout, B ends the session."""
    return _vr_wait_for_a_or_b(
        controller,
        "\n*** CLEAR THE ARM -- it will home when you start ***\n"
        "  A (or Enter) = run it | B = end the session",
        allow_enter=True,
    )


def _vr_wait_for_another(controller) -> bool:
    """Gate after labelling. A records another episode, B exits."""
    return _vr_wait_for_a_or_b(controller, "\n  A = record another episode | B = exit")


def _vr_label_outcome(controller, hold_secs: float, poll_s: float = 0.02) -> str:
    """Read one label off the controller. Returns an outcome slug or "discard".

    Two buttons have to cover five answers, so tap and hold are distinguished by
    hold_secs and both-at-once means discard. A hold resolves the moment the threshold
    is crossed (so you get told what you did without having to let go first); a tap
    resolves on release.
    """
    print(
        "  label it -- A tap = success | A hold = suboptimal | B tap = side effect | "
        "B hold = failure | A+B hold {:g}s = discard".format(hold_secs)
    )
    print("  (or type 1-5 + Enter here, if the headset is asleep: (1) success, "
          "(2) success suboptimal, (3) success side effect, (4) failure, (5) discard)")
    _wait_buttons_up(controller)
    warned_off = False
    a_since = None
    b_since = None
    both_since = None
    combo_seen = False
    while True:
        info = controller.get_info()
        if not info["controller_on"] and not warned_off:
            warned_off = True
            print("  WARNING: controller is not reporting -- is the headset awake? "
                  "You can type 1-5 + Enter instead.")
        try:
            typed = _stdin_label()
        except KeyboardInterrupt:
            print("\n  Ctrl+C ignored -- this episode is NOT saved yet. Label it.")
            continue
        if typed is not None:
            print(f"  typed -> {typed}")
            return typed
        a_down = bool(info["success"])
        b_down = bool(info["failure"])
        now = time.time()

        # Both down is checked first and wins over either alone, so rolling a thumb from
        # A onto B inside the hold window discards instead of labelling a success.
        if a_down and b_down:
            a_since = b_since = None
            if not combo_seen:
                combo_seen = True
                both_since = now
                print("  A+B down -- keep holding to discard...")
            elif now - both_since >= hold_secs:
                print("  discarded")
                return "discard"
            time.sleep(poll_s)
            continue

        # A half-finished discard is deliberately not a label: resolving it as whichever
        # button happened to come up last would silently save an episode you meant to
        # throw away. Start the prompt over instead.
        if combo_seen and not a_down and not b_down:
            combo_seen = False
            both_since = None
            print("  released before {:g}s -- not discarded, label it again".format(hold_secs))
            continue
        if combo_seen:
            time.sleep(poll_s)
            continue

        if a_down:
            if a_since is None:
                a_since = now
            elif now - a_since >= hold_secs:
                print("  A held -> success_suboptimal")
                return "success_suboptimal"
        elif a_since is not None:
            print("  A tapped -> success")
            return "success"

        if b_down:
            if b_since is None:
                b_since = now
            elif now - b_since >= hold_secs:
                print("  B held -> failure")
                return "failure"
        elif b_since is not None:
            print("  B tapped -> success_side_effect")
            return "success_side_effect"

        time.sleep(poll_s)


def main(args: Args):
    # Make sure external camera is specified by user -- we only use one external camera for the policy
    assert (
        args.external_camera is not None and args.external_camera in ["left", "right"]
    ), f"Please specify an external camera to use for the policy, choose from ['left', 'right'], but got {args.external_camera}"

    # Initialize the Panda environment. Using joint velocity action space and gripper position action space is very important.
    env = RobotEnv(action_space="joint_velocity", gripper_action_space="position")
    print("Created the droid env!")

    # Connect to the policy server
    policy_client = websocket_client_policy.WebsocketClientPolicy(args.remote_host, args.remote_port)

    df = pd.DataFrame(columns=["success", "duration", "video_filename"])

    # With --vr true the Quest controller drives start/stop/labelling. Everything is
    # still printed to the terminal -- nothing is displayed in the headset.
    use_vr = args.vr == "true"
    controller = _build_vr_controller() if use_vr else None

    # # == OopsieData project specific ==
    robot_profile = load_robot_profile("/app/semantic_corrections/robot_profiles/robot_profile.yaml")
    episode_recorder = EpisodeRecorder(
        robot_profile=robot_profile,
        data_root_dir=args.data_root_dir,
        resume_session_name=args.resume_session_name,
        operator_name=args.operator_name,
    )
    
    # Loop over n rollouts
    while True:

        # # == OopsieData project specific ==
        episode_recorder.reset_episode_recorder()
    
        instruction = input("Enter instruction: ").strip()
        while not instruction:
            # An empty instruction fails validation at save time, i.e. after the rollout
            # has already run and been labelled, and takes the episode down with it.
            instruction = input("Instruction cannot be empty. Enter instruction: ").strip()

        if use_vr:
            if not _vr_wait_to_start(controller):
                print("Ending the session.")
                break
            # All arm motion sits behind the start gate, so homing happens once you have
            # pressed A -- not while you are still typing the instruction.
            env.reset()

        # Rollout parameters
        actions_from_chunk_completed = 0
        pred_action_chunk = None

        # Prepare to save video of rollout
        timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        video = []
        bar = tqdm.tqdm(range(args.max_timesteps))
        if use_vr:
            print("Running rollout... press A (or B, or '{}', or Ctrl+C) to stop early "
                  "and keep the episode.".format(STOP_KEY))
        else:
            print("Running rollout... press '{}' (or Ctrl+C) to stop early and keep the "
                  "episode.".format(STOP_KEY))
        # The A that opened the start gate is usually still down; arming the latch on it
        # would stop the rollout on its first step.
        if use_vr:
            _wait_buttons_up(controller)
        stop_latch = _VRStopLatch(controller) if use_vr else None
        keys_live = _enable_single_keypresses()
        for t_step in bar:
            start_time = time.time()
            try:
                # Both stop paths keep the episode: they break out to the label prompt,
                # exactly as a completed rollout does. Checked inside the try so a Ctrl+C
                # landing here is caught below rather than escaping the loop.
                if stop_latch is not None and stop_latch.pressed:
                    print("\n{} pressed -- stopping the rollout, the episode is kept.".format(
                        stop_latch.pressed))
                    break
                if keys_live and _poll_keypress() == STOP_KEY:
                    print("\n'{}' pressed -- stopping the rollout, the episode is kept.".format(
                        STOP_KEY))
                    break
                # Get the current observation
                curr_obs = _extract_observation(
                    args,
                    env.get_observation(),
                    # Save the first observation to disk
                    save_to_disk=t_step == 0,
                )

                video.append(curr_obs[f"{args.external_camera}_image"])

                # Send websocket request to policy server if it's time to predict a new chunk
                if actions_from_chunk_completed == 0 or actions_from_chunk_completed >= args.open_loop_horizon:
                    actions_from_chunk_completed = 0

                    # We resize images on the robot laptop to minimize the amount of data sent to the policy server
                    # and improve latency.
                    request_data = {
                        "observation/exterior_image_1_left": image_tools.resize_with_pad(
                            curr_obs[f"{args.external_camera}_image"], 224, 224
                        ),
                        "observation/wrist_image_left": image_tools.resize_with_pad(curr_obs["wrist_image"], 224, 224),
                        "observation/joint_position": curr_obs["joint_position"],
                        "observation/gripper_position": curr_obs["gripper_position"],
                        "prompt": instruction,
                    }

                    # Wrap the server call in a context manager to prevent Ctrl+C from interrupting it
                    # Ctrl+C will be handled after the server call is complete
                    with prevent_keyboard_interrupt():
                        # this returns action chunk [10, 8] of 10 joint velocity actions (7) + gripper position (1)
                        pred_action_chunk = policy_client.infer(request_data)["actions"]
                        print(pred_action_chunk.shape)
                    # assert pred_action_chunk.shape == (10, 8)

                # Select current action to execute from chunk
                action = pred_action_chunk[actions_from_chunk_completed]
                actions_from_chunk_completed += 1

                # Binarize gripper action
                if action[-1].item() > 0.5:
                    # action[-1] = 1.0
                    action = np.concatenate([action[:-1], np.ones((1,))])
                else:
                    # action[-1] = 0.0
                    action = np.concatenate([action[:-1], np.zeros((1,))])

                # clip all dimensions of action to [-1, 1]
                action = np.clip(action, -1, 1)

                env.step(action)

                # # == OopsieData project specific ==
                episode_recorder.record_step(
                    observation={
                        "image_observation": {
                            "left": curr_obs["left_image"],
                            "right": curr_obs["right_image"],
                            "wrist": curr_obs["wrist_image"],
                        },
                        "robot_state": {
                            "cartesian_position": curr_obs["cartesian_position"],
                            "joint_position": curr_obs["joint_position"],
                            "gripper_position": curr_obs["gripper_position"],
                        },
                    },
                    action={
                        "joint_velocity": action[:-1],
                        "gripper_binary": action[-1],
                    },
                )

                # Sleep to match DROID data collection frequency
                elapsed_time = time.time() - start_time
                if elapsed_time < 1 / DROID_CONTROL_FREQUENCY:
                    time.sleep(1 / DROID_CONTROL_FREQUENCY - elapsed_time)
            except KeyboardInterrupt:
                break

        if stop_latch is not None:
            stop_latch.stop()
        _restore_keypresses()

        if video:
            video = np.stack(video)
        # ImageSequenceClip(list(video), fps=10).write_videofile(save_filename + ".mp4", codec="libx264")

        # == OopsieData project specific ==
        # A rollout stopped on the very first step has nothing to label, and saving it
        # would raise inside finish_rollout.
        # The validator rejects anything shorter than MIN_EPISODE_DURATION_S, so check it
        # here rather than letting the save fail once the rollout has been labelled.
        min_steps = math.ceil(MIN_EPISODE_DURATION_S * float(robot_profile.control_freq))
        if episode_recorder.num_steps < min_steps:
            print(
                "Only {} steps recorded (need {} for the {}s minimum) -- nothing to "
                "label or save.".format(episode_recorder.num_steps, min_steps, MIN_EPISODE_DURATION_S)
            )
        else:
            outcome = (
                _vr_label_outcome(controller, args.vr_hold_secs)
                if use_vr
                else _keyboard_label_outcome()
            )
            if outcome == "discard":
                print("  discarded -- nothing written to disk.")
            else:
                try:
                    _save_episode(episode_recorder, instruction, outcome, args.annotator_name)
                except Exception as exc:  # a rejected episode must not end the session
                    print(f"  FAILED TO SAVE -- episode lost: {type(exc).__name__}: {exc}")
        
        # df = df.append(
        #     {
        #         "success": success,
        #         "duration": t_step,
        #         "video_filename": save_filename,
        #     },
        #     ignore_index=True,
        # )
        # # New row as a DataFrame
        # new_row = pd.DataFrame([{
        #     "success": success,
        #     "duration": t_step,
        #     "video_filename": save_filename,
        # }])
        # df = pd.concat([df, new_row], ignore_index=True)

        if use_vr:
            if not _vr_wait_for_another(controller):
                break
        else:
            if input("Do one more eval? (enter y or n) ").lower() != "y":
                break
            env.reset()

    os.makedirs("results", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%I:%M%p_%B_%d_%Y")
    csv_filename = os.path.join("results", f"eval_{timestamp}.csv")
    df.to_csv(csv_filename)
    print(f"Results saved to {csv_filename}")


def _extract_observation(args: Args, obs_dict, *, save_to_disk=False):
    image_observations = obs_dict["image"]
    left_image, right_image, wrist_image = None, None, None
    # print(image_observations.keys())
    # breakpoint()
    for key in image_observations:
        # Note the "left" below refers to the left camera in the stereo pair.
        # The model is only trained on left stereo cams, so we only feed those.
        if args.left_camera_id in key and "left" in key:
            left_image = image_observations[key]
        elif args.right_camera_id in key and "left" in key:
            right_image = image_observations[key]
        elif args.wrist_camera_id in key and "left" in key:
            wrist_image = image_observations[key]

    # # FIXME: Temporary fix. Remove later
    right_image = left_image

    # Drop the alpha dimension
    left_image = left_image[..., :3]
    right_image = right_image[..., :3]
    wrist_image = wrist_image[..., :3]

    # Convert to RGB
    left_image = left_image[..., ::-1]
    right_image = right_image[..., ::-1]
    wrist_image = wrist_image[..., ::-1]

    # In addition to image observations, also capture the proprioceptive state
    robot_state = obs_dict["robot_state"]
    cartesian_position = np.array(robot_state["cartesian_position"])
    joint_position = np.array(robot_state["joint_positions"])
    gripper_position = np.array([robot_state["gripper_position"]])

    # Save the images to disk so that they can be viewed live while the robot is running
    # Create one combined image to make live viewing easy
    if save_to_disk:
        combined_image = np.concatenate([left_image, wrist_image, right_image], axis=1)
        combined_image = Image.fromarray(combined_image)
        combined_image.save("robot_camera_views.png")

    return {
        "left_image": left_image,
        "right_image": right_image,
        "wrist_image": wrist_image,
        "cartesian_position": cartesian_position,
        "joint_position": joint_position,
        "gripper_position": gripper_position,
    }


if __name__ == "__main__":
    args: Args = tyro.cli(Args)
    main(args)
    if args.vr == "true":
        # OculusReader.run() leaves a non-daemon adb-logcat thread that CPython waits on
        # at exit, so the process would otherwise hang here holding the cameras and the
        # headset. os._exit skips that wait.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
