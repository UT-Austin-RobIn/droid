#!/usr/bin/env python3
"""
Standalone script to visualize the live feed from connected ZED cameras.
Press 'q' in any window to quit.
"""

import argparse
import cv2
import numpy as np
import pyzed.sl as sl


def main():
    parser = argparse.ArgumentParser(description="Visualize ZED camera feeds")
    parser.add_argument(
        "--resolution",
        type=str,
        default="HD720",
        choices=["HD2K", "HD1080", "HD720", "VGA"],
        help="Camera resolution (default: HD720 for lighter display)",
    )
    parser.add_argument(
        "--view",
        type=str,
        default="LEFT",
        choices=["LEFT", "RIGHT"],
        help="Which eye to display (default: LEFT)",
    )
    args = parser.parse_args()

    res_map = {
        "HD2K": sl.RESOLUTION.HD2K,
        "HD1080": sl.RESOLUTION.HD1080,
        "HD720": sl.RESOLUTION.HD720,
        "VGA": sl.RESOLUTION.VGA,
    }
    view_map = {"LEFT": sl.VIEW.LEFT, "RIGHT": sl.VIEW.RIGHT}

    device_list = sl.Camera.get_device_list()
    print("device_list: ", device_list)
    if len(device_list) == 0:
        print("No ZED cameras detected.")
        return

    cameras = {}
    mats = {}
    window_names = []

    for i, device in enumerate(device_list):
        cam = sl.Camera()
        init_params = sl.InitParameters()
        init_params.camera_resolution = res_map[args.resolution]
        init_params.coordinate_units = sl.UNIT.MILLIMETER
        init_params.set_from_serial_number(device.serial_number)

        status = cam.open(init_params)
        if status != sl.ERROR_CODE.SUCCESS:
            print(f"Failed to open camera {i} (serial {device.serial_number}): {status}")
            continue

        cameras[device.serial_number] = cam
        mats[device.serial_number] = sl.Mat()
        # name = f"ZED {device.serial_number}"
        # window_names.append(name)
        # cv2.namedWindow(name, cv2.WINDOW_NORMAL)

    if not cameras:
        print("Could not open any camera.")
        return

    print(f"Showing {len(cameras)} ZED camera(s). Press 'q' in a window to quit.")
    view = view_map[args.view]

    try:
        while True:
            for cam_id, cam in cameras.items():
                if cam.grab() != sl.ERROR_CODE.SUCCESS:
                    continue
                cam.retrieve_image(mats[cam_id], view)
                img = mats[cam_id].get_data()
                # ZED returns BGRA; OpenCV imshow expects BGR
                if img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                # cv2.imshow(f"ZED {cam_id}", img)
                cv2.imwrite(f"ZED_{cam_id}.png", img)
                cv2.waitKey(10)

            # if cv2.waitKey(1) & 0xFF == ord("q"):
            #     break
    finally:
        # cv2.destroyAllWindows()
        for cam in cameras.values():
            cam.close()
        print("Done.")


if __name__ == "__main__":
    main()
