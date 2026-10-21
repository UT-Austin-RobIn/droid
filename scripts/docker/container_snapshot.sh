#!/bin/bash
# Snapshot / restore a running DROID laptop container so it survives a machine shutdown.
#
# IMPORTANT: a reboot does NOT destroy a container. `docker stop` + `docker start` keeps
# the writable layer intact. What destroys it is `docker rm` / `docker compose down`
# (which scripts/setup/laptop_setup_and_run.sh runs unconditionally). Use this script if
# you want a real, named snapshot you can always come back to.
#
# Usage:
#   ./container_snapshot.sh snapshot [CONTAINER] [TAG]   # commit container -> image + replayable run script
#   ./container_snapshot.sh restore  [TAG]               # recreate a container from a snapshot
#   ./container_snapshot.sh list                         # list snapshots taken by this script
#   ./container_snapshot.sh export   [TAG]               # write a portable .tar.gz of a snapshot
#
# Run this on the HOST (it needs the docker CLI), not inside the container.

set -euo pipefail

SNAP_REPO="droid_laptop_snapshot"
SNAP_DIR="${DROID_SNAPSHOT_DIR:-$HOME/.droid_snapshots}"

die() { echo "error: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker CLI not found. Run this on the host, not inside the container."

# Pick the single running container if the user didn't name one.
guess_container() {
  local ids
  ids=$(docker ps --format '{{.Names}}')
  [ -z "$ids" ] && die "no running containers"
  if [ "$(echo "$ids" | wc -l)" -ne 1 ]; then
    echo "Multiple running containers; pass one explicitly:" >&2
    echo "$ids" | sed 's/^/  /' >&2
    exit 1
  fi
  echo "$ids"
}

# Rebuild a `docker run` command line from `docker inspect`, so the restored container
# has the same mounts, devices, network, runtime and env as the original.
# docker commit saves the filesystem only -- never the run configuration.
emit_run_script() {
  local container="$1" image="$2" out="$3"
  docker inspect "$container" | python3 -c '
import json, shlex, sys

c = json.load(sys.stdin)[0]
hc, cfg = c["HostConfig"], c["Config"]
image = sys.argv[1]
name  = c["Name"].lstrip("/")

a = ["docker", "run", "-d", "--name", name]
if cfg.get("Tty"):       a.append("-t")
if cfg.get("OpenStdin"): a.append("-i")
if hc.get("Privileged"): a.append("--privileged")
if hc.get("Runtime") not in (None, "", "runc"): a += ["--runtime", hc["Runtime"]]
if hc.get("NetworkMode") not in (None, "", "default"): a += ["--network", hc["NetworkMode"]]
if hc.get("ShmSize"):    a += ["--shm-size", str(hc["ShmSize"])]
if hc.get("IpcMode"):    a += ["--ipc", hc["IpcMode"]]
for cap in hc.get("CapAdd") or []:  a += ["--cap-add", cap]
for g in hc.get("GroupAdd") or []:  a += ["--group-add", g]

# Mounts: replay bind mounts and named volumes exactly as given.
for b in hc.get("Binds") or []:
    a += ["-v", b]
for m in c.get("Mounts") or []:
    if m.get("Type") == "volume" and not any(
        (m.get("Name") or "") in b for b in (hc.get("Binds") or [])):
        a += ["-v", "%s:%s" % (m["Name"], m["Destination"])]

for d in hc.get("Devices") or []:
    spec = "%s:%s" % (d["PathOnHost"], d["PathInContainer"])
    if d.get("CgroupPermissions") not in (None, "", "rwm"):
        spec += ":" + d["CgroupPermissions"]
    a += ["--device", spec]

# Device requests (nvidia --gpus); runtime=nvidia covers the older path.
using_nvidia_runtime = hc.get("Runtime") == "nvidia"
for dr in (hc.get("DeviceRequests") or []) if not using_nvidia_runtime else []:
    if (dr.get("Driver") or "") in ("nvidia", ""):
        a += ["--gpus", "all" if dr.get("Count") == -1 else str(dr.get("Count") or "all")]
        break

for e in cfg.get("Env") or []:
    a += ["-e", e]
for h in hc.get("ExtraHosts") or []:
    a += ["--add-host", h]
if cfg.get("WorkingDir"): a += ["-w", cfg["WorkingDir"]]
if cfg.get("User"):       a += ["-u", cfg["User"]]
if hc.get("RestartPolicy", {}).get("Name") not in (None, "", "no"):
    a += ["--restart", hc["RestartPolicy"]["Name"]]

# The committed image carries the original ENTRYPOINT/CMD, but pin them so the
# restored container is identical even if the image config drifts.
ep = cfg.get("Entrypoint")
if ep: a += ["--entrypoint", ep[0] if len(ep) == 1 else json.dumps(ep)]
a.append(image)
a += cfg.get("Cmd") or []

# Group flag/value pairs onto one line each so the script stays readable.
lines, i = [], 0
while i < len(a):
    tok = a[i]
    if tok.startswith("-") and i + 1 < len(a) and not a[i + 1].startswith("-"):
        lines.append("%s %s" % (tok, shlex.quote(a[i + 1])))
        i += 2
    else:
        lines.append(shlex.quote(tok))
        i += 1
print(" \\\n  ".join(lines))
' "$image" > "$out.cmd"

  {
    echo "#!/bin/bash"
    echo "# Auto-generated from \`docker inspect $container\` on $(date -Iseconds)."
    echo "# Recreates the container from snapshot image: $image"
    echo "set -euo pipefail"
    echo
    echo "NAME=$(docker inspect -f '{{.Name}}' "$container" | sed 's|^/||')"
    echo
    echo '# X11 forwarding: /tmp/.docker.xauth does not survive a reboot, so rebuild it'
    echo '# before starting, or GUI windows from the container will fail to open.'
    echo 'XAUTH=/tmp/.docker.xauth'
    echo 'if [ -n "${DISPLAY:-}" ]; then'
    echo '  touch "$XAUTH"'
    echo '  xauth nlist "$DISPLAY" | sed -e "s/^..../ffff/" | xauth -f "$XAUTH" nmerge - || true'
    echo 'fi'
    echo
    echo '# Remove any container already holding this name.'
    echo 'docker rm -f "$NAME" >/dev/null 2>&1 || true'
    echo
    cat "$out.cmd"
    echo
    echo 'echo "Started $NAME. Attach with: docker exec -it $NAME bash"'
  } > "$out"
  rm -f "$out.cmd"
  chmod +x "$out"
}

cmd_snapshot() {
  local container="${1:-$(guess_container)}"
  local tag="${2:-$(date +%Y%m%d-%H%M%S)}"
  local image="$SNAP_REPO:$tag"

  docker inspect "$container" >/dev/null 2>&1 || die "no such container: $container"
  mkdir -p "$SNAP_DIR"

  echo "Committing $container -> $image (this can take a while; the writable layer is several GB)"
  # --pause is the default; it freezes the container so the layer is consistent.
  docker commit "$container" "$image" >/dev/null

  docker inspect "$container" > "$SNAP_DIR/$tag.inspect.json"
  emit_run_script "$container" "$image" "$SNAP_DIR/restore-$tag.sh"

  cat <<EOF

Snapshot complete.
  image        $image
  size         $(docker image inspect -f '{{.Size}}' "$image" | numfmt --to=iec)
  run config   $SNAP_DIR/$tag.inspect.json
  restore with $SNAP_DIR/restore-$tag.sh
               (or: $0 restore $tag)

Note: bind-mounted host directories are NOT inside this image -- they live on the host
and persist on their own. The image captures the container's own writable layer
(conda env at /root/miniconda3, apt packages, and un-mounted files under /app).
EOF
}

cmd_restore() {
  local tag="${1:-}"
  if [ -z "$tag" ]; then
    tag=$(ls -1 "$SNAP_DIR"/restore-*.sh 2>/dev/null | sort | tail -1 | sed 's|.*/restore-||; s|\.sh$||')
    [ -z "$tag" ] && die "no snapshots found in $SNAP_DIR"
    echo "No tag given; using most recent snapshot: $tag"
  fi
  local script="$SNAP_DIR/restore-$tag.sh"
  [ -x "$script" ] || die "no restore script for tag '$tag' (expected $script)"
  exec "$script"
}

cmd_list() {
  echo "Snapshot images:"
  docker images "$SNAP_REPO" --format '  {{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' || true
  echo
  echo "Restore scripts in $SNAP_DIR:"
  ls -1 "$SNAP_DIR"/restore-*.sh 2>/dev/null | sed 's|^|  |' || echo "  (none)"
}

cmd_export() {
  local tag="${1:-}"
  [ -z "$tag" ] && die "usage: $0 export TAG"
  local image="$SNAP_REPO:$tag" out="$SNAP_DIR/$SNAP_REPO-$tag.tar.gz"
  docker image inspect "$image" >/dev/null 2>&1 || die "no such snapshot image: $image"
  echo "Writing $out (large; compresses the whole image)"
  docker save "$image" | gzip > "$out"
  echo "Done: $out ($(du -h "$out" | cut -f1))"
  echo "Load elsewhere with: gunzip -c $(basename "$out") | docker load"
}

case "${1:-}" in
  snapshot) shift; cmd_snapshot "$@" ;;
  restore)  shift; cmd_restore  "$@" ;;
  list)     shift; cmd_list     "$@" ;;
  export)   shift; cmd_export   "$@" ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 1 ;;
esac
