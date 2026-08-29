from pyzed.sl import Camera, InitParameters, InputType
import time

# List of your ZED camera serial numbers
zed_serials = [39406856, 36088355, 18659563]

# Store successfully opened Camera objects
cameras = []

# Configurable settings
MAX_RETRIES = 3
RETRY_DELAY = 3  # seconds between retries for same camera
INTER_CAMERA_DELAY = 2  # delay between different camera initializations

for serial in zed_serials:
    cam = Camera()
    init_params = InitParameters()
    input_type = InputType()
    input_type.set_from_serial_number(serial)
    init_params.input = input_type

    success = False
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n🟡 Attempting to open ZED camera: {serial} (Attempt {attempt}/{MAX_RETRIES})")
        status = cam.open(init_params)

        if str(status) == "SUCCESS":
            print(f"✅ Successfully opened camera {serial}")
            cameras.append(cam)
            success = True
            break
        else:
            print(f"❌ Failed to open camera {serial}: {status}")
            time.sleep(RETRY_DELAY)

    if not success:
        print(f"🚫 Giving up on camera {serial} after {MAX_RETRIES} attempts.")

    time.sleep(INTER_CAMERA_DELAY)

# Hold open (optional)
print("\n🎥 Camera initialization complete. Press Ctrl+C to exit.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n🔻 Closing all cameras...")
    for cam in cameras:
        cam.close()
