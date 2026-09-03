# Plato (laptop)

Work from `~/workspace/DROID_setup/droid`.

## Mental model

```
laptop_setup.sh → docker-compose-laptop.yaml → builds Dockerfile.test_laptop
→ runs laptop_setup container (image tag: ghcr.io/.../droid_laptop:${ROBOT_TYPE})
→ entrypoint.sh, then compose command (usually bash)
```

## Step 1: Attach into docker container

### Option 1: Attach to a running container

If you see a docker container running:

```bash
(openpi) robin@robin-plato:~/workspace/DROID_setup/droid$ docker ps
CONTAINER ID   IMAGE                                      COMMAND                  CREATED        STATUSPORTS     NAMES
/droid_laptop:panda   "/app/.docker/laptop…"   17 minutes ago   Exited (137) 3 minutes ago             semantic-corrections
```

Then attach:

```bash
cd /home/robin/workspace/arpit/droid/
source .docker/laptop/variables.sh
docker compose -f .docker/laptop/docker-compose-laptop.yaml exec laptop_setup bash
```

### Option 2: Restart an exited container

If you see a container here:

```bash
robin@robin-plato:~/workspace/DROID_setup/droid$ docker ps -a
CONTAINER ID   IMAGE                                      COMMAND                  CREATED         STATUS                       PORTS     NAMES
59c43770d4aa   ghcr.io/droid-dataset/droid_laptop:panda   "/app/.docker/laptop…"   23 hours ago    Exited (137) 7 seconds ago             laptop-laptop_setup-1
```

Then restart it:

```bash
cd /home/robin/workspace/arpit/droid/
source .docker/laptop/variables.sh
docker compose -f .docker/laptop/docker-compose-laptop.yaml start
docker exec -it semantic-corrections bash
```

If you hit an X11 / `.docker.xauth` mount error:

```bash
sudo rm -rf /tmp/.docker.xauth
sudo touch /tmp/.docker.xauth
docker start -ai laptop-laptop_setup-1
```

### Option 3: Create / recreate the container (FULL CREATE!)

Use when starting from scratch, changing mounts/env in compose, fixing networking/X11/ADB, or rebuilding the image:

```bash
sudo ./scripts/setup/laptop_setup.sh
```

### Option 4: Restart a running container
```bash
cd /home/robin/workspace/arpit/droid/
source .docker/laptop/variables.sh
docker compose -f .docker/laptop/docker-compose-laptop.yaml restart
docker exec -it semantic-corrections bash
```

## Step 2: Start openpi server

In a different terminal:

```bash
(base) robin@robin-plato:~/workspace/openpi$ uv run scripts/serve_policy.py policy:checkpoint --policy.config=pi05_droid --policy.dir=gs://openpi-assets/checkpoints/pi05_droid
```

## Step 3 (Installing libraries required for Openpi - do it only once, unless you are recreating the container.)

1. conda init
2. source ~/.bashrc
3. conda activate robot
4. export OPENPI_ROOT=/app/openpi
5. cd $OPENPI_ROOT/packages/openpi-client && pip install -e .
6. pip install tyro ; pip install moviepy
7. cd /app/oopsie-tools && pip install -e .
8. cd /app
8. (Pi0 client) python scripts/run_openpi.py --remote_host=0.0.0.0 --remote_port=8000 --external_camera="left"

## Notes

After changing mounts or compose:

1. Edit `.docker/laptop/docker-compose-laptop.yaml`
2. Re-run `sudo ./scripts/setup/laptop_setup.sh`
3. Attach again (option 1)
