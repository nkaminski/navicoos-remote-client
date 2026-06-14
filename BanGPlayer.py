#!/usr/bin/env python3

import logging
import sys
import socket
import time
import argparse
import struct
import signal
from typing import Dict, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import mpv
    from Cocoa import NSApplication
    from AppKit import NSEvent, NSKeyDown, NSEventMaskKeyDown, NSScreen
except ImportError:
    logging.error("Missing dependencies. Please ensure 'python-mpv' and 'pyobjc-framework-Cocoa' are installed.")
    sys.exit(1)

parser = argparse.ArgumentParser(description='Remote display for B&G Vulcan/Zeus MFD')
parser.add_argument('IP', type=str, help='IP adress of Zeus/Vulcan MFD')
parser.add_argument('-c', '--remotecontrold-port', default=6633, help='remotecontrold port number (6633)')
parser.add_argument('-r', '--rtsp-port', default=554, help='rtsp port number (554)')
parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
parser.add_argument('--client-id', type=str, default='00:11:22:33:44:55', help='Client MAC address for auth (e.g. 00:11:22:33:44:55)')
args = vars(parser.parse_args())

def build_auth_packet(mac_str: str, client_name: str = 'iPad') -> bytes:
    """Build auth packet with client MAC address and name."""
    mac_bytes = bytes.fromhex(mac_str.replace(':', ''))
    if len(mac_bytes) != 6:
        raise ValueError('MAC address must be 6 bytes (e.g. 00:11:22:33:44:55)')
    payload = struct.pack('>H 6s 32s', 0x0003, mac_bytes, client_name.encode('ascii'))
    return struct.pack('>H', len(payload)) + payload

PING_PACKET: bytes = struct.pack('>H H I', 6, 1, 0x4403D7C3)

if args['debug']:
    logging.getLogger().setLevel(logging.DEBUG)

# Static mapping: device button index -> (local keyboard key, function description)
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

keyCodes: Dict[str, int] = {}

def strip0(b: bytes) -> str:
    """Strip null bytes from a byte string and decode to ASCII."""
    return b.split(b"\x00", 1)[0].decode("ascii")

def parse_ping_reply(data: bytes) -> int:
    """Parse ping reply to extract device info, keycodes, and resolution."""
    global keyCodes
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

# mpv key name -> keyCodes key mapping
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

NS_UP_ARROW: str = chr(0xF700)
NS_DOWN_ARROW: str = chr(0xF701)

# Convert string keys directly instead of mapping numeric scancodes
CHAR_TO_MPV: Dict[str, str] = {
    '\x1b': 'ESC',
    '\r': 'ENTER',
}

mouseDown: bool = False

def handle_key_press(key_name: str) -> None:
    """Handle a key press event from mpv, send press+release to device."""
    mpv_key = key_name
    mapped = MPV_KEY_MAP.get(mpv_key)
    if mapped and mapped in keyCodes:
        keycode = keyCodes[mapped]
        logging.debug('Key press: %s -> keycode %d' % (mpv_key, keycode))
        try:
            s.send(keybytes(keycode, 1))  # press
            s.send(keybytes(keycode, 0))  # release
        except socket.error as err:
            logging.error('Error sending key: %s' % err)
    else:
        logging.debug('Unmapped key: %s' % mpv_key)

def handle_mouse(player: 'mpv.MPV', x: float, y: float, event_type: int) -> None:
    """Send touch event to device. event_type: 0=press, 1=move, 2=release."""
    try:
        dims = player.osd_dimensions
        if dims and dims.get('w', 0) > 0 and dims.get('h', 0) > 0:
            ml = dims.get('ml', 0)
            mr = dims.get('mr', 0)
            mt = dims.get('mt', 0)
            mb = dims.get('mb', 0)
            w = dims.get('w', 1280)
            h = dims.get('h', 720)
            
            video_w = w - ml - mr
            video_h = h - mt - mb
            
            if video_w > 0 and video_h > 0:
                nx = (x - ml) / video_w
                ny = (y - mt) / video_h
                ix = int(nx * 1280)
                iy = int(ny * 720)
            else:
                ix = int(x * 1280 / w)
                iy = int(y * 720 / h)
        else:
            vw = player.osd_width or 1280
            vh = player.osd_height or 720
            ix = int(x * 1280 / vw) if vw else int(x)
            iy = int(y * 720 / vh) if vh else int(y)
    except Exception:
        ix = int(x)
        iy = int(y)

    ix = max(0, min(1279, ix))
    iy = max(0, min(719, iy))
    
    logging.debug('Touch event=%d x=%d y=%d' % (event_type, ix, iy))
    try:
        s.send(touchbytes(int(time.monotonic() * 1000), ix, iy, event_type, 1))
    except socket.error as err:
        logging.error('Error sending touch: %s' % err)

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((args['IP'], args['remotecontrold_port']))
    s.settimeout(10)
    
    logging.debug('Connecting to remotecontrold...')
    s.send(PING_PACKET)
    
    # Receive ping reply and discover keycodes
    ping_reply = s.recv(4096)
    logging.debug('Ping reply: %d bytes' % len(ping_reply))
    parse_ping_reply(ping_reply)
    
    # Build and send auth with client MAC
    auth_pkt = build_auth_packet(args['client_id'])
    logging.debug('Sending authenticate with MAC %s...' % args['client_id'])
    
    logging.debug('Auth packet: %s' % auth_pkt.hex(' '))
    s.send(auth_pkt)
    
    # Receive auth acknowledgment
    auth_ack = s.recv(4096)
    logging.debug('Auth ack: %s' % auth_ack.hex(' '))
    if len(auth_ack) >= 4:
        ack_opcode = int.from_bytes(auth_ack[2:4], 'big')
        if ack_opcode == 0x0004:
            logging.debug('Auth acknowledged by device')
        else:
            logging.warning('Unexpected response opcode: 0x%04x' % ack_opcode)
            
except socket.timeout:
    logging.warning("Network timeout waiting for device response")
except socket.error:
    logging.exception("Connection error")
    sys.exit(1)

rtsp_url = f"rtsp://{args['IP']}:{args['rtsp_port']}/screenmirror"
logging.info('Opening RTSP stream via Cocoa NSApplication: %s' % rtsp_url)

app = NSApplication.sharedApplication()

# Check if main screen is HiDPI (Retina)
backing_scale = 1.0
if 'NSScreen' in globals():
    main_screen = NSScreen.mainScreen()
    if main_screen:
        backing_scale = main_screen.backingScaleFactor()

player = mpv.MPV(
    input_default_bindings=False,
    input_vo_keyboard=False,
    window_dragging=False,
    osc=False,
    title='B&G Remote Display',
    profile='low-latency',
    untimed=True,
    cache='no',
    video_margin_ratio_top=0.1,
    window_scale=2.0 if backing_scale > 1.0 else 1.0,
    log_handler=lambda level, component, message: logging.debug('[%s] %s', component, message),
    loglevel='warn'
)

def global_key_handler(event: 'NSEvent') -> 'NSEvent':
    """Global Cocoa key event monitor to intercept keys before they hit the window."""
    if event.type() == NSKeyDown:
        chars = event.charactersIgnoringModifiers()
        if not chars:
            return event
        char = chars[0]
        
        if char == 'q':
            logging.info('Quit requested')
            player.quit()
            return None
            
        if char == NS_UP_ARROW:
            mpv_key = 'UP'
        elif char == NS_DOWN_ARROW:
            mpv_key = 'DOWN'
        else:
            mpv_key = CHAR_TO_MPV.get(char, char)
            
        if mpv_key in MPV_KEY_MAP:
            handle_key_press(mpv_key)
            return None
    return event

# NSEventMaskKeyDown = 1024
NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown if 'NSEventMaskKeyDown' in globals() else 1024, global_key_handler)

# Mouse event handling mimicking original GStreamer behavior
@player.key_binding('MBTN_LEFT_DBL')
@player.key_binding('MBTN_LEFT')
def mouse_left_handler(state: str = 'p-', name: str = None, char: str = None, *_) -> None:
    global mouseDown
    is_down = (state[0] == 'd')
    is_up = (state[0] == 'u')
    is_press = (state[0] == 'p')

    if is_down or is_press:
        mouseDown = True
        try:
            mx = player.mouse_pos['x']
            my = player.mouse_pos['y']
            handle_mouse(player, mx, my, 0)
        except Exception as e:
            logging.debug('Mouse press error: %s' % e)
    
    if is_up or is_press:
        try:
            mx = player.mouse_pos['x']
            my = player.mouse_pos['y']
            handle_mouse(player, mx, my, 2)
        except Exception as e:
            logging.debug('Mouse release error: %s' % e)
        mouseDown = False

@player.property_observer('mouse-pos')
def on_mouse_move(name: str, value: Dict[str, float]) -> None:
    global mouseDown
    if mouseDown and value:
        mx = value.get('x', 0)
        my = value.get('y', 0)
        handle_mouse(player, mx, my, 1)

@player.event_callback('file-loaded')
def on_file_loaded(event: Dict) -> None:
    logging.info('Stream connected and playing')
    
@player.event_callback('shutdown')
@player.event_callback('end-file')
def stop_app(evt: Dict) -> None:
    logging.info('Shutting down Cocoa app')
    s.close()
    app.terminate_(None)

player.play(rtsp_url)

# Allow Ctrl+C in terminal to instantly kill the Cocoa app
signal.signal(signal.SIGINT, signal.SIG_DFL)
app.run()
