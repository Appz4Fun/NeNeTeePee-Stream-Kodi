# CoreELEC and Linux tuning

NeNeTeePee-Stream-Kodi runs well out of the box. This page is an **advanced, optional**
reference for a specific demanding scenario: 4K Dolby Vision playback running
concurrently with heavy TMDBHelper cache-warming on a low-memory ARM device.

None of this is required to use NeNeTeePee-Stream-Kodi. It documents a real, tested
configuration for a **CoreELEC box on an Amlogic S922X** (6-core big.LITTLE,
4 GB RAM, Samsung T5 SSD over USB 3.0), which you can adapt if you push a similar
device hard.

!!! note "Device-specific"
    These values were tuned for one hardware profile. Treat them as a worked
    example, not universal settings. CoreELEC uses a read-only squashfs root, so
    persistent tuning lives under `/storage/`, applied via
    `/storage/.config/autostart.sh` and systemd units in
    `/storage/.config/system.d/`.

## What gets tuned, and why

| Area | Change | Why it helped |
|------|--------|---------------|
| **USB storage (UAS)** | Load a cross-compiled `uas.ko` and rebind the SSD from `usb-storage` to UAS, raising queue depth from 1 to 30 | Parallel SCSI queuing dropped write latency from ~10 ms to ~6 ms; SQLite checkpoints flush across NAND channels instead of serially |
| **SSD hints** | `echo 0 > /sys/block/sda/queue/rotational` | Stops HDD heuristics (anticipatory merging, oversized readahead) that add latency on an SSD hidden behind a USB bridge |
| **Dirty pages** | Absolute limits: `dirty_background_bytes=64 MB`, `dirty_bytes=256 MB`, 1 s writeback, 10 s expiry | The default 20% ratio allowed ~760 MB of dirty pages, causing write stalls; absolute byte limits keep the dirty set shallow so `fsync()` returns quickly |
| **Network (RPS)** | `rps_cpus=3f` across all 6 cores + a flow table | The `meson6-dwmac` NIC has a single RX queue; nearly all NET_RX softirqs landed on one core, capping throughput under 90+ concurrent connections. RPS spreads packets across all cores |
| **TCP** | `tcp_tw_reuse=1`, `tcp_fin_timeout=30`, ephemeral port range `10240–65535` | Hundreds of short-lived HTTP connections per second exhausted the default port range in TIME_WAIT. Starting at 10240 keeps outgoing connections off service ports such as Kodi's 8080. Existing 16 MB socket buffers were already fine |
| **Memory margin** | `vm.min_free_kbytes=32 MB` | Kodi's peak reaches ~3.8 GB during 4K DV; the default 16 MB margin left almost no room before emergency reclaim |
| **zram swap** | 2 GB lz4 compressed swap, priority 100 | The S922X has no disk swap; at peak load this turns OOM kills into manageable swap pressure (~700 MB physical for 2 GB swap) |
| **Page-cache warmup** | Read `Textures13.db` into cache at boot | Kodi queries the 545 MB texture DB for every on-screen image; pre-warming turns cold ~0.5 ms SSD reads into ~0.001 ms RAM hits |
| **Process priority** | Warmup services set to `Nice=19`, best-effort I/O priority 7 | Keeps background warmup from competing with Kodi's I/O, without SIGSTOP throttling |
| **CPU governor** | `performance` on all cores (CoreELEC default) | Eliminates frequency-scaling latency for HDR tone mapping and UI bursts; power draw is irrelevant on a plugged-in device |

## Going further

These additions came from a second S922X box with the same Samsung T5. It's one
more worked example, so check each step against your own device.

### Move Kodi's database to the SSD

Bind-mount `userdata/Database` from the SSD so Kodi's paths don't change. On the
tested box, the main gain was taking database writes off the eMMC. A single 64 MB
`fsync` write test ran at 89 MB/s on the T5 and 71 MB/s on the eMMC, so don't
expect a large speed-up.

First, mount the SSD by UUID in `/storage/.config/system.d/storage-ssd.mount`:

```ini
[Unit]
Description=Mount the database SSD
Before=kodi.service

[Mount]
What=/dev/disk/by-uuid/<your-uuid>
Where=/storage/ssd
Type=ext4
Options=noatime,nofail

[Install]
WantedBy=multi-user.target
```

Start it and confirm the SSD is mounted. If it isn't, the copy in the next step
lands on the eMMC, and the SSD hides it once it mounts.

```bash
systemctl daemon-reload
systemctl enable --now storage-ssd.mount
grep " /storage/ssd " /proc/mounts   # expect the SSD's device
```

Next, stop Kodi, copy the folder, and check the copied databases:

```bash
systemctl stop kodi
mkdir -p /storage/ssd/kodi
cp -a /storage/.kodi/userdata/Database /storage/ssd/kodi/Database
for db in /storage/ssd/kodi/Database/*.db; do
  echo "$db: $(sqlite3 "$db" 'PRAGMA integrity_check;' | head -1)"   # expect ok
done
```

Then add the bind mount in
`/storage/.config/system.d/storage-.kodi-userdata-Database.mount`. The file name
has to match the mount path, which `systemd-escape -p --suffix=mount <path>` prints.

```ini
[Unit]
Description=Bind Kodi's database onto the SSD
Requires=storage-ssd.mount
After=storage-ssd.mount
Before=kodi.service

[Mount]
What=/storage/ssd/kodi/Database
Where=/storage/.kodi/userdata/Database
Type=none
Options=bind,nofail

[Install]
WantedBy=kodi.service
```

Run `systemctl daemon-reload`, enable the bind mount, reboot, and check the order with
`journalctl -b -o short-monotonic | grep -E "Mounted storage-|Starting kodi"`.

!!! warning "Keep `nofail` on both units"
    Without `nofail` on the bind mount, the tested box stalled for 90 seconds at
    boot. The SSD didn't appear until the wait timed out, so neither mount came up
    and Kodi started on the old eMMC database.

If the SSD is missing at boot, Kodi falls back to the out-of-date copy on the eMMC.
Anything else that writes under `/storage/ssd` then writes to the eMMC, underneath
the mount point. Clean that up before the SSD mounts again.

`x-systemd.device-timeout` is only read from `fstab`, so it has no effect in a
`.mount` file. To shorten the 90-second device wait, add a drop-in for the device
unit, whose name `systemd-escape -p --suffix=device /dev/disk/by-uuid/<your-uuid>`
prints, with `JobRunningTimeoutSec=30` under `[Unit]`. This setting hasn't been
tested with the SSD unplugged.

### Turn on TRIM over USB

The kernel left TRIM off for the T5 (`provisioning_mode` was `full`, and
`discard_max_bytes` was 0), so `fstrim` reported that discard isn't supported.
Enabling `unmap` fixed it. Add a udev rule, using your own drive's IDs from `lsusb`,
in `/storage/.config/udev.rules.d/60-ssd-trim.rules`:

```text
ACTION=="add|change", SUBSYSTEM=="scsi_disk", ATTRS{idVendor}=="04e8", ATTRS{idProduct}=="61f5", ATTR{provisioning_mode}="unmap"
```

To try it by hand on kernel 4.9, use `printf unmap`, not `echo unmap`. The
trailing newline from `echo` makes the write fail with `Invalid argument`. Some USB
bridges handle TRIM badly, so run `fstrim -v` once by hand before you rely on it.

### Use a UAS opt-in if your build has one

The tested CoreELEC 21.3 build didn't need a cross-compiled `uas.ko`. It ships
`/usr/lib/libreelec/uas-boot`, which rebinds drives at boot when
`/storage/.config/uas.enabled` exists. To keep one drive on `usb-storage`, list
its `VID:PID` in `/storage/.config/uas-blacklist.txt`.

The rebind gives the drive a new device name, so the T5 moved from `sda` to `sdb`.
Mount by UUID, and don't hard-code `sda` in scripts. The rebind also logged a few
I/O errors on the old `sda` at each boot on the tested box, while the filesystem
stayed clean.

### Test a drive before you trust it

An NVMe drive in a USB adapter on the same box connected and reported the right
size, but its reads failed with `Illegal Request` or hung until the kernel took it
offline. The cause wasn't found. Before you move a database to a new drive, read
from it and watch `dmesg` for resets and I/O errors.

## Verifying

The drive may not be `sda` after the UAS rebind, so these commands use a wildcard.

```bash
cat /sys/block/sd*/device/queue_depth         # expect 30 (UAS active)
cat /sys/block/sd*/queue/rotational           # expect 0 (treated as an SSD)
cat /sys/block/sd*/queue/discard_max_bytes    # expect non-zero (TRIM available)
cat /sys/class/net/eth0/queues/rx-0/rps_cpus  # expect 3f (RPS active)
lsmod | grep uas                              # expect uas loaded
grep userdata/Database /proc/mounts           # expect the SSD (database moved)
```

## How this relates to NeNeTeePee-Stream-Kodi

NeNeTeePee-Stream-Kodi doesn't apply or depend on any of the preceding OS tuning. Nothing in the
add-on changes kernel, sysctl, or systemd settings. The tuning is a separate
layer that keeps storage and the network responsive when Kodi and background
warmup services compete for a small pool of RAM and a single-queue NIC. On a
roomier device (more RAM, a multi-queue NIC, native SATA), most of it isn't
needed.

These NeNeTeePee-Stream-Kodi settings address playback smoothness at the app layer
(**Playback backend** tab, with nzbdav / InfiniDysk selected):

- **Read-ahead buffer size in MB** (default 256, 0 turns it off). Keeps
  filling while playback is paused.
- **Max seconds to wait for a slow/stalled backend before giving up** (default
  120, 0 turns it off).

See [Stream proxy](../how-it-works/stream-proxy.md) for how they work. Both
were added in 2.0.0-beta.1 and are available on the
[Beta channel](../getting-started/beta-channel.md).

The one Kodi-side change NeNeTeePee-Stream-Kodi itself recommends is in
`advancedsettings.xml`: set `<cache><memorysize>0</memorysize></cache>` so
large files can play in pass-through mode with full seeking on 32-bit Kodi
builds. NeNeTeePee-Stream-Kodi only suggests this in a dialog; it never edits the file. See
[advancedsettings.xml and seeking](../reference/advancedsettings.md).
