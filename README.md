# Nelko P21 label printer script

This repository contains a reverse-engineered Python script for the Nelko P21 Bluetooth label printer.

The script can print image labels, render and print text labels, and read or change basic printer settings without using the official Nelko app.

This version connects directly to the printer using a Python Bluetooth RFCOMM socket. It does **not** require:

* `/dev/rfcomm0`
* the `rfcomm` command
* `bluez-deprecated-tools`
* a manually bound serial device

The printer still has to be paired with the computer through Bluetooth first.

## Supported platform

This script is intended for Linux systems with BlueZ Bluetooth support.

It was tested for a setup where Python can open Bluetooth RFCOMM sockets directly via:

```python
socket.AF_BLUETOOTH
socket.BTPROTO_RFCOMM
```

This is especially useful on systems like Manjaro or Arch Linux, where the old `rfcomm` command may no longer be available by default.

## Requirements

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

For the current script, the required Python runtime packages are:

```bash
pip install pillow packaging
```

System packages usually needed on Manjaro / Arch:

```bash
sudo pacman -S bluez bluez-utils
```

Make sure Bluetooth is running:

```bash
sudo systemctl enable --now bluetooth
```

## Pair the printer
<b>TODO: describe better</b>

Turn on the Nelko P21 printer and pair it with your computer.

Start `bluetoothctl`:

```bash
bluetoothctl
```

Inside `bluetoothctl`:

```text
power on
agent on
default-agent
scan on
```

Wait until the printer appears. It is usually shown as something like `P21`.

Then pair and trust it:

```text
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
quit
```

Replace `XX:XX:XX:XX:XX:XX` with the Bluetooth MAC address of your printer.

You can list paired devices with:

```bash
bluetoothctl devices
```

## Script usage

The printer works over Bluetooth Classic using the SPP/RFCOMM protocol.

Unlike the original version, this script does not require a serial device such as `/dev/rfcomm0`. Instead, pass the printer MAC address directly to the script:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --status
```

Show debug output:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --status --debug
```

Read battery status:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --battery
```

Read printer configuration:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --config
```

Run a self-test print:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --selftest
```

Print an image:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --image test-template.png
```

Print text:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --text "Hello Nelko"
```

Print text with density and copy count:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --text "Hello Nelko" --density 15 --copies 1
```

Print an image with density and copy count:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --image test-template.png --density 15 --copies 1
```

Use a different RFCOMM channel:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --bt-channel 1 --status
```

Channel `1` is the default and normally works for the Nelko P21.

If both `--image` and `--text` are provided, the script prints both labels: first the image, then the rendered text label.

## Command line options

```text
--bt-address   Bluetooth MAC address of the printer. Required.
--bt-channel   Bluetooth RFCOMM channel. Default: 1.
--image        Image file to print.
--text         Render the given text onto a 14x40 mm label and print it.
--density      Print density/darkness from 1 to 15. Default: 15.
--copies       Number of copies. Default: 1.
--status       Read printer status.
--battery      Read battery level.
--config       Read printer configuration.
--timeout      Set timeout: 0, 15, 30 or 60 minutes.
--beep         Enable or disable beep: on/off.
--selftest     Run printer self-test.
--debug        Show debug output.
```

## Examples

### Check connection

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --status --debug
```

Expected output is something like:

```text
Printer status:
Ready
Label Type: 14x40mm(Gapped), White color
```

### Print the included test template

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --image test-template.png --debug
```

### Print a simple text label

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --text "Storage box"
```

### Print a multi-line text label

Use shell quoting that preserves line breaks, for example:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --text $'Line 1\nLine 2'
```

### Print multiple copies of a text label

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --text "Cable box" --copies 3
```

### Disable beep

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --beep off
```

### Enable beep

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --beep on
```

### Set timeout to 15 minutes

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --timeout 15
```

### Disable timeout

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --timeout 0
```

## Image and text format

The Nelko P21 uses a small monochrome print area. The default label format used by this script is 14 x 40 mm.

The printer bitmap is:

```text
96 x 284 pixels
1-bit black/white
12 bytes per row
284 rows
3408 bytes total
```

### Image printing

When `--image` is used, the script converts the input image to:

* grayscale
* high contrast
* 1-bit black/white
* a printer-compatible bitmap

The image is resized to fit the default 14x40 mm label format.

For best results, use simple black-and-white images or high-contrast PNG files.

### Text printing

When `--text` is used, the script first renders the text to an internal label image and then sends it through the same bitmap print path as image printing.

Text rendering uses:

* 14 x 40 mm label size
* 2 mm horizontal margin
* 1 mm vertical margin
* automatic font size selection
* minimum font height of about 3 mm
* automatic line wrapping for text wider than about 36 mm
* centered text alignment
* DejaVu Sans if available, otherwise Pillow's default font

Very long text may become hard to read because it has to fit into the 14 x 40 mm label area.

## Troubleshooting

<b>TODO: describe better</b>
### `Bluetooth socket error: [Errno 111] Connection refused`

The printer is probably not paired, not trusted, already connected somewhere else, or the wrong RFCOMM channel is used.

Try:

```bash
bluetoothctl
devices
info XX:XX:XX:XX:XX:XX
```

Then reconnect:

```bash
remove XX:XX:XX:XX:XX:XX
scan on
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
quit
```

### `No route to host`

The printer may be off, asleep, out of range, or Bluetooth may not be running.

Try:

```bash
sudo systemctl restart bluetooth
bluetoothctl power on
```

Then turn the printer off and on again.

### `Address already in use`

Another process may still be connected to the printer.

Close the official Nelko app on your phone and make sure no other script instance is running.

### `ModuleNotFoundError: No module named 'PIL'`

Install Pillow:

```bash
pip install pillow
```

### `ModuleNotFoundError: No module named 'packaging'`

Install packaging:

```bash
pip install packaging
```

### Text does not use the expected font

The script tries to load DejaVu Sans from common Linux font paths. If it cannot find it, it falls back to Pillow's default font.

Install DejaVu fonts if you want more predictable text rendering:

```bash
sudo pacman -S ttf-dejavu
```

### The printer prints blank labels

Try a higher density:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --image test-template.png --density 15
```

Also make sure the label roll is inserted correctly and the printer has detected the label type.

### The script connects but receives no useful response

Try debug mode:

```bash
python p21_print.py --bt-address XX:XX:XX:XX:XX:XX --status --debug
```

Also check whether the printer is still connected to your phone. The printer usually accepts only one active Bluetooth connection at a time.

## Protocol notes

The printer communicates over Bluetooth Classic SPP/RFCOMM.

The printer uses proprietary commands and a subset of TSPL2-like commands.

Known commands include:

```text
BATTERY?
CONFIG?
TIMEOUT
BEEP
SELFTEST
SIZE
GAP
DIRECTION
DENSITY
CLS
BITMAP
PRINT
```

Most text commands are followed by CRLF:

```text
\r\n
```

The print command uses raw bitmap data after the `BITMAP` command.

The default label format used by this script is:

```text
SIZE 14.0 mm,40.0 mm
GAP 5.0 mm,0 mm
DIRECTION 1,1
DENSITY 15
CLS
BITMAP 0,0,12,284,1,<raw bitmap data>
PRINT 1
```

The image data is 96 x 284 pixels with 1-bit color depth.

## Difference from the original script
https://github.com/merlinschumacher/nelko-p21-print.git

The original workflow required creating an RFCOMM serial device first:

```bash
rfcomm connect /dev/rfcomm0 XX:XX:XX:XX:XX:XX
```

This version skips that step.

Old workflow:

```text
Bluetooth printer
  -> rfcomm command
  -> /dev/rfcomm0
  -> pyserial
  -> p21_print.py
```

New workflow:

```text
Bluetooth printer
  -> Python RFCOMM socket
  -> p21_print.py
```

This makes the script easier to use on distributions where the old `rfcomm` command is missing or deprecated.

## Security and privacy note

This script communicates locally with the printer over Bluetooth.

No official Nelko app account is required.
No cloud connection is required by this script.

##
##


Use this script at your own risk.

This project is based on reverse engineering of the Nelko P21 Bluetooth protocol.

