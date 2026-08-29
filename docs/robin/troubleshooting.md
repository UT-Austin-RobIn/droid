# Troubleshooting & tips

Misc notes that don't belong in the Plato or NUC setup guides.

## Saving a docker image

1. `docker commit laptop-laptop_setup-1 semantic-corrections:v1`
2. Verify: `docker images | grep semantic-correcitons`
3. Point compose at that image. In `.docker/laptop/docker-compose-laptop.yaml`, change:

```bash
image: ghcr.io/droid-dataset/droid_laptop:${ROBOT_TYPE}
```

to:

```bash
image: droid_laptop:panda-robin
```

## Common errors

1. **Socket closed** — happens often; re-run the command and it usually works.
2. **ZED camera error** — lower fps in `droid/camera_utils/camera_readers`:

```python
standard_params = dict(
    depth_minimum_distance=0.1,
    camera_resolution=sl.RESOLUTION.HD720,
    depth_stabilization=False,
    camera_fps=30,
    camera_image_flip=sl.FLIP_MODE.OFF,
)
```

3. **OpenCV issues** — uninstall `opencv` and `opencv-contrib`, then reinstall. DROID needs only `opencv-contrib-python==4.6.0.66`.

## Debugging ZED

```bash
lsusb | grep -i zed
```

## Git LFS push failure

If push fails with permission denied on `.git/lfs/cache`:

```bash
sudo chown -R robin:robin .git/lfs
git push
```
