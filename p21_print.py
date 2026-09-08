#!/usr/bin/env python3
"""
Nelko P21 Bluetooth label printer script using a direct Python RFCOMM socket.

No /dev/rfcomm0 required.
No rfcomm command required.

Example:
    python p21_print_socket.py --bt-address XX:XX:XX:XX:XX:XX --status
    python p21_print_socket.py --bt-address XX:XX:XX:XX:XX:XX --battery
    python p21_print_socket.py --bt-address XX:XX:XX:XX:XX:XX --image test-template.png
"""

import argparse
import socket
import struct
import sys
import time
from enum import IntEnum

from packaging.version import Version
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


LABEL_WIDTH_MM = 14.0
LABEL_LENGTH_MM = 40.0
LABEL_WIDTH_PX = 96
LABEL_LENGTH_PX = 284
PX_PER_MM_W = LABEL_LENGTH_PX / LABEL_LENGTH_MM   # along 40 mm axis
PX_PER_MM_H = LABEL_WIDTH_PX / LABEL_WIDTH_MM     # along 14 mm axis


def _load_font(size_px):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size_px)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _line_height(font):
    if isinstance(font, ImageFont.FreeTypeFont):
        ascent, descent = font.getmetrics()
        return ascent + descent

    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]


def _wrap_text(text, font, max_width_px, draw):
    """Wrap text to fit max_width_px, breaking on spaces, then on chars if needed."""
    lines = []

    for paragraph in text.splitlines() or [""]:
        words = paragraph.split(" ")
        current = ""

        for word in words:
            candidate = word if not current else current + " " + word

            if draw.textlength(candidate, font=font) <= max_width_px:
                current = candidate
                continue

            if current:
                lines.append(current)

            # word itself too wide: break per character
            if draw.textlength(word, font=font) > max_width_px:
                buf = ""
                for ch in word:
                    if draw.textlength(buf + ch, font=font) <= max_width_px:
                        buf += ch
                    else:
                        if buf:
                            lines.append(buf)
                        buf = ch
                current = buf
            else:
                current = word

        lines.append(current)

    return lines


def render_text_image(text):
    """
    Render `text` onto a 14x40 mm label image (284x96 px).

    Constraints:
    - 1 mm horizontal margin (left/right), 0 mm vertical margin (top/bottom)
    - Font auto-sized to fit; minimum text height 3 mm
    - Maximum 3 text lines
    - Lines that exceed available width are wrapped
    """
    width_px = LABEL_LENGTH_PX
    height_px = LABEL_WIDTH_PX

    margin_x = int(round(1.0 * PX_PER_MM_W))   # ~7 px margin on left and right
    margin_y = 0 # no vertical margin, to maximize text height
    max_text_w = width_px - 2 * margin_x
    max_text_h = height_px - 2 * margin_y
    min_font_px = max(1, int(round(3.0 * PX_PER_MM_H)))  # ~21 px
    max_lines = 3
    
    image = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(image)

    text = text if text else ""

    # Try font sizes from large to small; pick the largest that fits.
    max_font_px = max_text_h
    chosen_font = None
    chosen_lines = None
    chosen_metrics = None

    for size in range(max_font_px, min_font_px - 1, -1):
        font = _load_font(size)
        lines = _wrap_text(text, font, max_text_w, draw)

        if len(lines) > max_lines:
            continue

        line_h = _line_height(font)
        gap = max(1, line_h // 10) if len(lines) > 1 else 0
        total_h = line_h * len(lines) + gap * (len(lines) - 1)

        widest = max(
            (draw.textlength(line, font=font) for line in lines),
            default=0,
        )

        if total_h <= max_text_h and widest <= max_text_w:
            chosen_font = font
            chosen_lines = lines
            chosen_metrics = (line_h, gap, total_h)
            break

    if chosen_font is None:
        min_font = _load_font(min_font_px)
        min_font_lines = _wrap_text(text, min_font, max_text_w, draw)

        if len(min_font_lines) > max_lines:
            raise ValueError(
                f"Text too long: requires {len(min_font_lines)} lines, maximum is {max_lines}."
            )

        raise ValueError("Text could not be fitted into label height with up to 3 lines.")

    assert chosen_font is not None
    assert chosen_lines is not None
    assert chosen_metrics is not None

    line_h, gap, total_h = chosen_metrics
    y = margin_y + max(0, (max_text_h - total_h) // 2)

    for line in chosen_lines:
        line_w = draw.textlength(line, font=chosen_font)
        x = margin_x + max(0, (max_text_w - int(line_w)) // 2)
        draw.text((x, y), line, font=chosen_font, fill=0)
        y += line_h + gap

    return image


DEBUG = False

BT_ADDRESS = None
BT_CHANNEL = 1
SOCKET_TIMEOUT = 1.0
CONNECT_TIMEOUT = 8.0


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x1:  # If LSB is 1
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    # Convert the 16-bit integer to a 2-byte array in big-endian format.
    return crc.to_bytes(2, byteorder="big")


def validate_checksum(data):
    if not data or len(data) < 2:
        raise ValueError(f"Invalid response, too short: {data!r}")

    provided_checksum = data[-2:]
    computed_checksum = crc16(data[:-2])

    if provided_checksum != computed_checksum:
        raise ValueError(
            f"Invalid checksum: {provided_checksum.hex()} != {computed_checksum.hex()}"
        )


class TimeoutSetting(IntEnum):
    NEVER = 0
    MINUTES_15 = 1
    MINUTES_30 = 2
    MINUTES_60 = 3

    def __str__(self):
        if self == TimeoutSetting.NEVER:
            return "Never"
        if self == TimeoutSetting.MINUTES_15:
            return "15 minutes"
        if self == TimeoutSetting.MINUTES_30:
            return "30 minutes"
        if self == TimeoutSetting.MINUTES_60:
            return "60 minutes"
        return "Unknown"


class BeepSetting(IntEnum):
    OFF = 0
    ON = 1

    def __str__(self):
        if self == BeepSetting.ON:
            return "On"
        if self == BeepSetting.OFF:
            return "Off"
        return "Unknown"


class PaperType(IntEnum):
    CONTINUOUS = 0
    GAPPED = 1
    BLACKMARK = 2

    def __str__(self):
        if self == PaperType.GAPPED:
            return "Gapped"
        if self == PaperType.CONTINUOUS:
            return "Continuous"
        if self == PaperType.BLACKMARK:
            return "Blackmark"
        return "Unknown"


class PrinterReadinessStatus(IntEnum):
    READY = 0
    LID_OPEN = 1
    OUT_OF_PAPER = 4
    BUSY = 32

    def __str__(self):
        if self == PrinterReadinessStatus.READY:
            return "Ready"
        if self == PrinterReadinessStatus.LID_OPEN:
            return "Lid Open"
        if self == PrinterReadinessStatus.OUT_OF_PAPER:
            return "Paper not loaded"
        if self == PrinterReadinessStatus.BUSY:
            return "Busy"
        return "Unknown"


class PaperColor(IntEnum):
    UNKNOWN = 0
    TRANSPARENT = 2
    WHITE = 3
    PINK = 4
    BLUE = 5
    YELLOW = 6

    def __str__(self):
        if self == PaperColor.TRANSPARENT:
            return "Transparent"
        if self == PaperColor.WHITE:
            return "White"
        if self == PaperColor.PINK:
            return "Pink"
        if self == PaperColor.BLUE:
            return "Blue"
        if self == PaperColor.YELLOW:
            return "Yellow"
        return "Unknown"


class DeviceConfig:
    def __init__(self, data):
        self.dpi_resolution = data[0]
        self.hardware_version = Version(f"{data[1]}.{data[2]}.{data[3]}")
        self.second_firmware_version = Version(f"{data[4]}.{data[5]}.{data[6]}")
        self.timeout_setting = TimeoutSetting(data[7])
        self.beep_setting = BeepSetting(data[8])

    def __str__(self):
        return (
            f"DPI Resolution: {self.dpi_resolution}\n"
            f"Hardware Version: {self.hardware_version}\n"
            f"Second Firmware Version: {self.second_firmware_version}\n"
            f"Timeout: {self.timeout_setting}\n"
            f"Beep: {self.beep_setting}"
        )


class BatteryData:
    def __init__(self, data):
        self.battery_level = ((data[0] >> 4) & 0x0F) * 10 + (data[0] & 0x0F)
        self.charging = data[1]

    def __str__(self):
        charging_text = "Charging" if self.charging else "Not Charging"

        if self.charging:
            return (
                f"Battery Level: {self.battery_level}%\n"
                f"Charging: {charging_text}\n"
                f"Unplug the printer to get a current battery reading."
            )

        return (
            f"Battery Level: {self.battery_level}%\n"
            f"Charging: {charging_text}"
        )


class PrinterStatus:
    def __init__(self, data):
        self.raw = bytes(data)

        if len(self.raw) != 16:
            raise ValueError(
                f"Invalid printer status length: expected 16, got {len(self.raw)}"
            )

        self.payload = self.raw[:14]
        self.checksum = self.raw[14:16]

        self.printer_status = PrinterReadinessStatus(self.raw[0])
        self.data_length = self.raw[1]

        self.unknown_flags_1 = self.raw[2]
        self.unknown_flags_2 = self.raw[3]

        self.label_color = PaperColor(self.raw[4])

        self.unknown_flags_3 = self.raw[5]

        self.border_radius = self.raw[6]
        self.paper_type = PaperType(self.raw[7])

        self.unknown_media_flags_1 = self.raw[8]
        self.unknown_media_flags_2 = self.raw[9]
        self.unknown_media_flags_3 = self.raw[10]

        self.label_length = self.raw[11]
        self.maximum_label_width = self.raw[12]
        self.label_width = self.raw[13]

    def __str__(self):
        text = f"{self.printer_status}\n"

        if self.label_width == 0 and self.label_length == 0:
            text += "The printer found no readable RFID tag."
        else:
            text += (
                f"Label Type: {self.label_width}x{self.label_length}mm"
                f"({self.paper_type}), {self.label_color} color\n"
            )

        if DEBUG:
            text += (
                "\nDebug status bytes:\n"
                f"Byte 01 / index 00 / printer_status:        0x{self.raw[0]:02x} ({self.raw[0]}) -> {self.printer_status}\n"
                f"Byte 02 / index 01 / data_length:           0x{self.raw[1]:02x} ({self.raw[1]})\n"
                f"Byte 03 / index 02 / unknown_flags_1:       0x{self.raw[2]:02x} ({self.raw[2]})\n"
                f"Byte 04 / index 03 / unknown_flags_2:       0x{self.raw[3]:02x} ({self.raw[3]})\n"
                f"Byte 05 / index 04 / label_color:           0x{self.raw[4]:02x} ({self.raw[4]}) -> {self.label_color}\n"
                f"Byte 06 / index 05 / unknown_flags_3:       0x{self.raw[5]:02x} ({self.raw[5]})\n"
                f"Byte 07 / index 06 / border_radius:         0x{self.raw[6]:02x} ({self.raw[6]})\n"
                f"Byte 08 / index 07 / paper_type:            0x{self.raw[7]:02x} ({self.raw[7]}) -> {self.paper_type}\n"
                f"Byte 09 / index 08 / unknown_media_flags_1: 0x{self.raw[8]:02x} ({self.raw[8]})\n"
                f"Byte 10 / index 09 / unknown_media_flags_2: 0x{self.raw[9]:02x} ({self.raw[9]})\n"
                f"Byte 11 / index 10 / unknown_media_flags_3: 0x{self.raw[10]:02x} ({self.raw[10]})\n"
                f"Byte 12 / index 11 / label_length:          0x{self.raw[11]:02x} ({self.raw[11]}) mm\n"
                f"Byte 13 / index 12 / maximum_label_width:   0x{self.raw[12]:02x} ({self.raw[12]}) mm\n"
                f"Byte 14 / index 13 / label_width:           0x{self.raw[13]:02x} ({self.raw[13]}) mm\n"
                f"Byte 15 / index 14 / crc_byte_1:            0x{self.raw[14]:02x} ({self.raw[14]})\n"
                f"Byte 16 / index 15 / crc_byte_2:            0x{self.raw[15]:02x} ({self.raw[15]})\n"
                f"Raw status:   {self.raw.hex()}\n"
                f"Payload:      {self.payload.hex()}\n"
                f"Checksum:     {self.checksum.hex()}\n"
            )

        return text


def open_bt_socket():
    if not BT_ADDRESS:
        raise ValueError("No Bluetooth address set. Use --bt-address XX:XX:XX:XX:XX:XX")

    sock = socket.socket(
        socket.AF_BLUETOOTH,
        socket.SOCK_STREAM,
        socket.BTPROTO_RFCOMM,
    )

    sock.settimeout(CONNECT_TIMEOUT)
    sock.connect((BT_ADDRESS, BT_CHANNEL))
    sock.settimeout(SOCKET_TIMEOUT)

    return sock


def read_response(sock, timeout=SOCKET_TIMEOUT, max_bytes=8192):
    """
    Liest die Antwort vom Drucker.

    Der Drucker antwortet je nach Befehl entweder mit ASCII + CRLF
    oder mit kurzen Binärdaten. Deshalb lesen wir bis Timeout.
    """
    sock.settimeout(timeout)
    data = bytearray()

    while len(data) < max_bytes:
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            break

        if not chunk:
            break

        data += chunk

        if data.endswith(b"\r\n"):
            break

        # Statusantwort ist normalerweise 16 Bytes.
        if len(data) >= 16 and not data.startswith((b"BATTERY ", b"CONFIG ")):
            break

    return bytes(data)


def send_command(command, encode=True, response_timeout=SOCKET_TIMEOUT):
    if encode:
        if isinstance(command, bytes):
            payload = command
        else:
            payload = command.encode()

        if not payload.endswith(b"\r\n"):
            payload += b"\r\n"
    else:
        payload = command

    if DEBUG:
        print(f"Sending {len(payload)} bytes")
        print(f"TX: {payload[:80].hex()}{'...' if len(payload) > 80 else ''}")

    try:
        with open_bt_socket() as sock:
            sock.sendall(payload)
            time.sleep(0.05)
            response = read_response(sock, timeout=response_timeout)

            if DEBUG:
                print(f"Received {len(response)} bytes")
                print(f"RX: {response.hex()}")

            return response

    except OSError as e:
        print(f"Bluetooth socket error: {e}")
        return b""


def clean_response(response, prefix, expected_len):
    if not response:
        raise ValueError("Empty response from printer")

    prefix_bytes = prefix.encode()

    if not response.startswith(prefix_bytes):
        raise ValueError(f"Invalid response prefix: {response.hex()}")

    if response.endswith(b"\r\n"):
        cleaned = response[len(prefix_bytes):-2]
    else:
        cleaned = response[len(prefix_bytes):]

    if len(cleaned) != expected_len:
        raise ValueError(
            f"Invalid response length: expected {expected_len}, got {len(cleaned)}: "
            f"{response.hex()}"
        )

    return cleaned


def unpack_printer_status(status):
    if len(status) > 16:
        status = status[:16]

    if len(status) != 16:
        raise ValueError(f"Invalid status length: expected 16, got {len(status)}: {status.hex()}")

    unpacked_status = struct.unpack(">BBBBBBBBBBBBBBBB", status)
    return PrinterStatus(unpacked_status)


def get_printer_status():
    status = send_command(b"\x1b!o")
    validate_checksum(status[:16])
    return unpack_printer_status(status[:16])


def get_readiness_status():
    response = send_command(b"\x1b!?")

    if not response:
        raise ValueError("No readiness response from printer")

    return PrinterReadinessStatus(response[0])


def get_config():
    response = send_command("CONFIG?")
    configdata = clean_response(response, "CONFIG ", 10)
    unpacked_data = struct.unpack(">hBBBBBBB?", configdata)
    return DeviceConfig(unpacked_data)


def get_battery():
    response = send_command("BATTERY?")
    batterydata = clean_response(response, "BATTERY ", 2)

    if DEBUG:
        print(f"Battery raw data: {batterydata.hex()}")

    unpacked_data = struct.unpack(">B?", batterydata)
    return BatteryData(unpacked_data)


def get_timeout_command(timeout):
    if timeout == 0:
        timeout_setting = TimeoutSetting.NEVER
    elif timeout == 15:
        timeout_setting = TimeoutSetting.MINUTES_15
    elif timeout == 30:
        timeout_setting = TimeoutSetting.MINUTES_30
    elif timeout == 60:
        timeout_setting = TimeoutSetting.MINUTES_60
    else:
        raise ValueError("Invalid timeout setting. Must be 0, 15, 30 or 60.")

    return f"TIMEOUT {chr(timeout_setting.value)}"


def get_beep_command(beep):
    beep_setting = BeepSetting.ON if beep else BeepSetting.OFF
    return f"BEEP {chr(beep_setting.value)}"


def load_image(image_path):
    if isinstance(image_path, Image.Image):
        image = image_path
    else:
        image = Image.open(image_path)

    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)

    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2)

    if image.width > image.height:
        image = image.rotate(90, expand=True)

    image.thumbnail((96, 284), Image.Resampling.NEAREST)
    image = image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

    bitdata = image.tobytes()

    # 96 px Breite = 12 Bytes pro Zeile.
    # 284 Zeilen = 3408 Bytes.
    if len(bitdata) < 3408:
        bitdata = bitdata.ljust(3408, b"\xff")

    return bitdata


def build_print_command(imagedata, density, copies):
    if not 1 <= density <= 15:
        raise ValueError("Density must be between 1 and 15.")

    if copies < 1:
        raise ValueError("Copies must be at least 1.")

    data = b""
    data += b"\x1b!o\r\n"
    data += b"SIZE 14.0 mm,40.0 mm\r\n"
    data += b"GAP 5.0 mm,0 mm\r\n"
    data += b"DIRECTION 1,1\r\n"
    data += f"DENSITY {density}\r\n".encode()
    data += b"CLS\r\n"
    data += b"BITMAP 0,0,12,284,1,"
    data += imagedata
    data += f"\r\nPRINT {copies}\r\n".encode()

    return data


def print_image(image_path, density, copies):
    bitdata = load_image(image_path)
    print_command = build_print_command(bitdata, density, copies)

    response = send_command(print_command, encode=False, response_timeout=2.0)

    if response:
        try:
            validate_checksum(response[:16])
            status = unpack_printer_status(response[:16])

            if DEBUG or status.printer_status != PrinterReadinessStatus.READY:
                print(status)

        except Exception as e:
            print(f"Print command sent, but response could not be parsed: {e}")
            if DEBUG:
                print(response.hex())
    else:
        print("Print command sent, but no response received.")


def parse_bool(value):
    value = str(value).strip().lower()

    if value in ("1", "true", "yes", "on", "enable", "enabled"):
        return True

    if value in ("0", "false", "no", "off", "disable", "disabled"):
        return False

    raise argparse.ArgumentTypeError("Use on/off, true/false or 1/0.")


def main():
    parser = argparse.ArgumentParser(
        description="Print images and manage settings on a Nelko P21 via direct Bluetooth RFCOMM socket."
    )

    parser.add_argument(
        "--bt-address",
        required=True,
        help="Bluetooth MAC address of the printer, e.g. XX:XX:XX:XX:XX:XX",
    )

    parser.add_argument(
        "--bt-channel",
        type=int,
        default=1,
        help="Bluetooth RFCOMM channel, defaults to 1.",
    )

    parser.add_argument(
        "--image",
        help="Image file to print.",
    )

    parser.add_argument(
        "--text",
        help="Render the given text onto a 14x40 mm label and print it.",
    )

    parser.add_argument(
        "--density",
        type=int,
        default=15,
        help="Print density/darkness, 1-15. Default: 15.",
    )

    parser.add_argument(
        "--copies",
        type=int,
        default=1,
        help="Number of copies. Default: 1.",
    )

    parser.add_argument(
        "--config",
        action="store_true",
        help="Get printer configuration.",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Get printer status.",
    )

    parser.add_argument(
        "--battery",
        action="store_true",
        help="Get printer battery level.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        choices=[0, 15, 30, 60],
        help="Set printer timeout in minutes: 0, 15, 30 or 60.",
    )

    parser.add_argument(
        "--beep",
        type=parse_bool,
        help="Enable or disable beep: on/off.",
    )

    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run a self-test print.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output.",
    )

    args = parser.parse_args()

    global DEBUG, BT_ADDRESS, BT_CHANNEL
    DEBUG = args.debug
    BT_ADDRESS = args.bt_address
    BT_CHANNEL = args.bt_channel

    if DEBUG:
        print("Debug mode enabled.")
        print(f"Using Bluetooth RFCOMM socket: {BT_ADDRESS}, channel {BT_CHANNEL}")

    did_something = False

    try:
        if args.status:
            did_something = True
            print("Printer status:")
            print(get_printer_status())

        if args.battery:
            did_something = True
            print("Printer battery status:")
            print(get_battery())

        if args.config:
            did_something = True
            print("Printer configuration:")
            print(get_config())

        if args.timeout is not None:
            did_something = True
            command = get_timeout_command(args.timeout)
            print(f"Setting timeout to {args.timeout} minutes.")
            send_command(command)
            print(get_config())

        if args.beep is not None:
            did_something = True
            command = get_beep_command(args.beep)
            print(f"Setting beep to {'on' if args.beep else 'off'}.")
            send_command(command)
            print(get_config())

        if args.selftest:
            did_something = True
            print("Running self-test print.")
            send_command("SELFTEST", response_timeout=2.0)

        if args.image:
            did_something = True
            print_image(args.image, args.density, args.copies)

        if args.text:
            did_something = True
            text_image = render_text_image(args.text)
            print_image(text_image, args.density, args.copies)

        if not did_something:
            parser.print_help()

    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(130)

    except Exception as e:
        print(f"Error: {e}")

        if DEBUG:
            raise

        sys.exit(1)


if __name__ == "__main__":
    main()
