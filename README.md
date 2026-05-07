# EnvertecVictron

Integrates Envertech micro inverters into Victron Venus OS / Cerbo GX by publishing Envertech Portal production data as a Victron `com.victronenergy.pvinverter` D-Bus service.

The inverter then appears in the Venus OS device list, overview, Modbus-TCP and VRM like a normal PV inverter.

## Features

- Reads live production data from the Envertech Portal API
- Publishes PV inverter values to Victron D-Bus
- Supports configurable D-Bus device instance and phase assignment
- Exposes AC power, voltage, current and daily energy
- Can survive Venus OS firmware updates by adding itself to `/data/rc.local`
- Uses HTTP request timeouts so portal/network stalls do not permanently freeze the D-Bus service

## Requirements

- Victron Venus OS device, e.g. Cerbo GX
- SSH root access to the Venus OS device
- Internet access from the Venus OS device
- Envertech Portal station ID
- Python with Victron `velib_python` available on Venus OS

## Installation

SSH into the Cerbo GX / Venus OS device as `root` and install under `/data` so the files persist across firmware updates:

```bash
ssh root@<cerbo-ip>
cd /data
wget -O EnvertecVictron-main.zip \
  https://github.com/darvader/EnvertecVictron/archive/refs/heads/main.zip
unzip EnvertecVictron-main.zip
mv EnvertecVictron-main EnvertecVictron
cd EnvertecVictron
vi config.ini
chmod +x install.sh
./install.sh
```

If your Venus OS image has `git` installed, you can also clone the repository directly instead:

```bash
cd /data
git clone https://github.com/darvader/EnvertecVictron.git
cd EnvertecVictron
vi config.ini
./install.sh
```

The installer will:

1. Set executable permissions on helper scripts
2. Download and unpack the bundled Python wheel dependencies
3. Create the service symlink:

   ```bash
   /service/dbus-envertech-pvinverter -> /data/EnvertecVictron/service
   ```

4. Add the installer to `/data/rc.local` so the service link is recreated after Venus OS updates or reboots.

## Configuration

Edit `config.ini` before or after installation:

```ini
[DEFAULT]
StationId=<yourStationId>
Serial=1234567890
FirmwareVersion=0.1
Phase=L1
SignOfLifeLog=1
Deviceinstance=41
CustomName=Envertech PV Inverter
Position=0
UpdateInterval=60
```

### Configuration options

| Option | Description |
| --- | --- |
| `StationId` | Envertech Portal station ID. Required. |
| `Serial` | Serial number shown in Venus OS / VRM. Can be any stable value. |
| `FirmwareVersion` | Firmware version shown on D-Bus. Currently informational. |
| `Phase` | AC phase used for the PV inverter: `L1`, `L2` or `L3`. |
| `SignOfLifeLog` | Interval in minutes for periodic log messages. Use `0` to disable. |
| `Deviceinstance` | Victron D-Bus device instance. Service name becomes `tcp_<Deviceinstance>`, e.g. `tcp_42`. |
| `CustomName` | Name displayed in Venus OS. |
| `Position` | Victron PV inverter position. Usually `0` for AC input / grid side, depending on your setup. |
| `UpdateInterval` | Poll interval in seconds for Envertech Portal data. Default: `60`. |

After changing `config.ini`, restart the service:

```bash
svc -t /service/dbus-envertech-pvinverter
```

If `svc` is unavailable in your shell, rebooting the Cerbo GX also reloads the service.

## Usage

Once installed and running, the service registers itself on D-Bus as:

```text
com.victronenergy.pvinverter.tcp_<Deviceinstance>
```

Example for `Deviceinstance=42`:

```text
com.victronenergy.pvinverter.tcp_42
```

You should see the inverter in:

- Venus OS device list
- Venus OS overview
- VRM dashboard
- Modbus-TCP using the configured device instance

### Check service status

```bash
ps | grep -i '[e]nvertech\|[p]vinverter'
```

### Check logs

```bash
tail -f /data/EnvertecVictron/current.log
```

Typical healthy log entries look like:

```text
INFO registered ourselves on D-Bus as com.victronenergy.pvinverter.tcp_42
INFO Connected to dbus, and switching over to gobject.MainLoop()
INFO Last '/Ac/Power': 234.65
```

### Query D-Bus manually

```bash
dbus-send --system --print-reply \
  --dest=com.victronenergy.pvinverter.tcp_42 \
  /Ac/Power com.victronenergy.BusItem.GetValue

dbus-send --system --print-reply \
  --dest=com.victronenergy.pvinverter.tcp_42 \
  /Connected com.victronenergy.BusItem.GetValue
```

Expected examples:

```text
variant double 234.65
variant int32 1
```

## Updating

Most Venus OS images do not include `git`, so the safest update method is to download the latest ZIP and keep your existing `config.ini`:

```bash
ssh root@<cerbo-ip>
cd /data
cp EnvertecVictron/config.ini /tmp/envertech-config.ini
wget -O EnvertecVictron-main.zip \
  https://github.com/darvader/EnvertecVictron/archive/refs/heads/main.zip
rm -rf EnvertecVictron-main EnvertecVictron-new
unzip EnvertecVictron-main.zip
mv EnvertecVictron-main EnvertecVictron-new
cp /tmp/envertech-config.ini EnvertecVictron-new/config.ini
mv EnvertecVictron EnvertecVictron.bak-$(date +%Y%m%d-%H%M%S)
mv EnvertecVictron-new EnvertecVictron
cd EnvertecVictron
./install.sh
svc -t /service/dbus-envertech-pvinverter
```

If you installed with `git`, updating is simpler:

```bash
cd /data/EnvertecVictron
git pull
./install.sh
svc -t /service/dbus-envertech-pvinverter
```

## Uninstall

```bash
ssh root@<cerbo-ip>
cd /data/EnvertecVictron
./uninstall.sh
```

You may also remove the matching `/data/rc.local` line manually if you no longer want the service recreated after firmware updates.

## Troubleshooting

### Inverter does not appear in Venus OS

Check that the service symlink exists:

```bash
ls -l /service/dbus-envertech-pvinverter
```

It should point to:

```text
/data/EnvertecVictron/service
```

If missing, run:

```bash
cd /data/EnvertecVictron
./install.sh
```

### D-Bus service exists but values are stale

Check the log:

```bash
tail -n 100 /data/EnvertecVictron/current.log
```

The bridge depends on the Envertech Portal API. If the portal is unavailable, the service logs the request error and tries again during the next update cycle.

### `/Connected` becomes `0`

The service marks itself disconnected if it has not received successful Envertech data for more than 5 minutes. This usually means:

- Envertech Portal is down or slow
- The Cerbo GX has no internet access
- `StationId` is wrong
- The portal response format changed

### Service hangs or does not restart cleanly

Try a manual restart:

```bash
svc -t /service/dbus-envertech-pvinverter
```

If needed, kill the Python process and let supervision restart it:

```bash
kill $(pgrep -f /data/EnvertecVictron/dbus-envertech-pvinverter.py)
```

## Screenshots

Screenshots are available in the [`img/`](img/) directory.

## License

See [`LICENSE`](LICENSE).
