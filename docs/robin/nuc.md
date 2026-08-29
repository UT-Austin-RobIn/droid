# NUC

Work from `~/droid` on the NUC.

> Remember to attach the Robotiq gripper to the NUC.

> Ensure that deoxys launch commands are killed (check if any tmux sessions are running it, if so terminate the script inside)

## Initial setup

```bash
sudo ./scripts/setup/nuc_setup.sh
```

Expected container:

```bash
(base) robin@robin-plato-nuc:~/droid$ docker ps
CONTAINER ID   IMAGE                                   COMMAND                  CREATED      STATUS      PORTS     NAMES
5f784ac53d25   ghcr.io/droid-dataset/droid_nuc:panda   "/app/scripts/server…"   9 days ago   Up 6 days             nuc-setup_nuc-1
```

Then attach to the container.

## Start robot + gripper (inside container)

1. `conda activate polymetis_local`
2. `launch_robot.py robot_client=franka_hardware`
3. `bash /app/droid/franka/launch_gripper.sh`

To stop the container:

```bash
sudo docker compose -f docker-compose-nuc.yaml down
```

## Robotiq gripper width fix

**Issue:**

```
File "/app/droid/franka/robot.py", line 38, in launch_robot
  self._max_gripper_width = self._gripper.metadata.max_width
AttributeError: 'GripperInterface' object has no attribute 'metadata'
```

**Fix** — in `droid/franka/robot.py`, hardcode gripper max width:

```python
# self._max_gripper_width = self._gripper.metadata.max_width
self._max_gripper_width = 0.08
```

For the change to take effect:

```bash
sudo docker restart nuc-setup_nuc-1
```

## Optional settings (if you run into errors)

- Set `gripper_max_width` to a constant (same fix as above)
- Restart the container: `sudo docker restart nuc-setup_nuc-1`

## Expected processes

Inside the NUC container, `ps aux | grep franka` should show robot and gripper launch scripts running, plus:

```
/root/miniconda3/envs/polymetis-local/bin/python3.8 .../launch_robot.py robot_client=franka_hardware
/app/droid/fairo/polymetis/polymetis/build/franka_panda_client /tmp/tmpeqid3sa0
```

Full example output:

```
(polymetis-local) root@robin-plato-nuc:/app# ps aux | grep franka
root        9580  0.0  0.0  18612  3176 ?        S    00:50   0:00 /bin/bash -c echo robot_123 | sudo -S bash /app/droid/franka/launch_gripper.sh
root        9588  0.0  0.0  47936  3632 ?        S    00:50   0:00 sudo -S bash /app/droid/franka/launch_gripper.sh
root        9589  0.0  0.0  10156  3000 ?        S    00:50   0:00 bash /app/droid/franka/launch_gripper.sh
(polymetis-local) root@robin-plato-nuc:/app# ps aux | grep franka
root       19652  0.0  0.0  18612  3208 ?        S    20:56   0:00 /bin/bash -c echo robot_123 | sudo -S bash /app/droid/franka/launch_robot.sh
root       19660  0.0  0.0  47936  3548 ?        S    20:56   0:00 sudo -S bash /app/droid/franka/launch_robot.sh
root       19661  0.0  0.0  10156  2956 ?        S    20:56   0:00 bash /app/droid/franka/launch_robot.sh
root       19669  0.0  0.0  18612  3132 ?        S    20:56   0:00 /bin/bash -c echo robot_123 | sudo -S bash /app/droid/franka/launch_gripper.sh
root       19677  0.0  0.0  47936  3576 ?        S    20:56   0:00 sudo -S bash /app/droid/franka/launch_gripper.sh
root       19678  0.0  0.0  10156  2992 ?        S    20:56   0:00 bash /app/droid/franka/launch_gripper.sh
root       19688  0.2  2.9 603964 230112 ?       S    20:56   0:01 /root/miniconda3/envs/polymetis-local/bin/python3.8 /root/miniconda3/envs/polymetis-local/bin/launch_robot.py robot_client=franka_hardware
root       19713  0.0  0.0  47936  3680 ?        S    20:56   0:00 sudo env "PATH=$PATH" /app/droid/fairo/polymetis/polymetis/build/franka_panda_client /tmp/tmpeqid3sa0
root       19714  6.0  1.0 420752 79448 ?        SLl  20:56   0:29 /app/droid/fairo/polymetis/polymetis/build/franka_panda_client /tmp/tmpeqid3sa0
root       19783  0.0  0.0  11472  1156 pts/0    R+   21:04   0:00 grep --color=auto franka
```
