import struct
import logging
from typing import Dict, Tuple

PING_PACKET: bytes = struct.pack('>H H I', 6, 1, 0x4403D7C3)

BUTTON_MAP: Dict[int, Tuple[str, str]] = {
    0x01: ('Escape', 'Page'),
    0x02: ('m', 'Menu'),
    0x03: ('Up', 'Zoom In'),
    0x04: ('Down', 'Zoom Out'),
    0x05: ('p', 'Power'),
    0x07: ('Return', 'Enter'),
    0x08: ('c', 'Cancel'),
    0x09: ('o', 'MOB'),
    0x0a: ('g', 'Goto'),
    0x0b: ('a', 'Mark'),
    0x0c: ('w', 'WheelKey'),
}

MPV_KEY_MAP: Dict[str, str] = {
    'ESC': 'Escape',
    'm': 'm',
    'UP': 'Up',
    'DOWN': 'Down',
    'p': 'p',
    'ENTER': 'Return',
    'c': 'c',
    'g': 'g',
    'a': 'a',
    'o': 'o',
    'w': 'w',
}

CHAR_TO_MPV: Dict[str, str] = {
    '\x1b': 'ESC',
    '\r': 'ENTER',
}

def build_auth_packet(mac_str: str, client_name: str = 'iPad') -> bytes:
    """Build auth packet with client MAC address and name."""
    mac_bytes = bytes.fromhex(mac_str.replace(':', ''))
    if len(mac_bytes) != 6:
        raise ValueError('MAC address must be 6 bytes (e.g. 00:11:22:33:44:55)')
    payload = struct.pack('>H 6s 32s', 0x0003, mac_bytes, client_name.encode('ascii'))
    return struct.pack('>H', len(payload)) + payload

def strip0(b: bytes) -> str:
    """Strip null bytes from a byte string and decode to ASCII."""
    return b.split(b"\x00", 1)[0].decode("ascii")

def parse_ping_reply(data: bytes, keyCodes: Dict[str, int]) -> int:
    """Parse ping reply to extract device info, keycodes, and resolution."""
    # Skip length (2) + opcode (2)
    payload = data[4:]
    pingid, str1_b, str2_b, version_b = struct.unpack_from('>I 32s 32s 24s', payload, 0)
    str1 = strip0(str1_b)
    str2 = strip0(str2_b)
    version = strip0(version_b)

    logging.info('Device: %s (%s), Version: %s' % (str1, str2, version))

    # Keycode table starts at payload offset 92
    count = payload[92]
    logging.info('Device reports %d buttons' % count)

    logging.info("Discovered keycodes from device:")
    for i in range(count):
        offset = 93 + i * 8
        btn_index, keycode = struct.unpack_from('>I I', payload, offset)

        if btn_index in BUTTON_MAP:
            key, func = BUTTON_MAP[btn_index]
            keyCodes[key] = keycode
            logging.info("  %s\t\t%s\t(keycode %d)" % (key, func, keycode))
        else:
            logging.warning('  Unknown button index 0x%02x -> keycode %d' % (btn_index, keycode))

    # Resolution follows the keycode table
    res_offset = 93 + count * 8
    if len(payload) >= res_offset + 4:
        width, height = struct.unpack_from('>H H', payload, res_offset)
        logging.info('Display resolution: %dx%d' % (width, height))

    return pingid

def touchbytes(timestamp: int, x_coord: int, y_coord: int, event_type: int, touch_count: int) -> bytes:
    """Generate a touch event packet."""
    payload = struct.pack('>H I H H B B', 0x1001, timestamp, x_coord, y_coord, event_type, touch_count)
    return struct.pack('>H', len(payload)) + payload

def keybytes(keycode: int, pressRelease: int) -> bytes:
    """Generate a key event packet."""
    payload = struct.pack('>H I I', 0x1003, keycode, pressRelease)
    return struct.pack('>H', len(payload)) + payload
