#!/usr/bin/env python3

import logging
import sys
import socket
import time
import argparse
import signal
from typing import Dict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import mpv
    from Cocoa import NSApplication
except ImportError:
    logging.error("Missing dependencies. Please ensure 'python-mpv' and 'pyobjc-framework-Cocoa' are installed.")
    sys.exit(1)

from .core import (
    build_auth_packet, parse_ping_reply, touchbytes, keybytes,
    PING_PACKET, MPV_KEY_MAP
)
from .macos import get_backing_scale, setup_global_key_monitor

def main():
    parser = argparse.ArgumentParser(description='Remote display for B&G Vulcan/Zeus MFD')
    parser.add_argument('IP', type=str, help='IP adress of Zeus/Vulcan MFD')
    parser.add_argument('-c', '--remotecontrold-port', default=6633, help='remotecontrold port number (6633)')
    parser.add_argument('-r', '--rtsp-port', default=554, help='rtsp port number (554)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
    parser.add_argument('--client-id', type=str, default='00:11:22:33:44:55', help='Client MAC address for auth (e.g. 00:11:22:33:44:55)')
    args = vars(parser.parse_args())

    if args['debug']:
        logging.getLogger().setLevel(logging.DEBUG)

    keyCodes: Dict[str, int] = {}
    mouseDown: bool = False

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((args['IP'], args['remotecontrold_port']))
        s.settimeout(10)
        
        logging.debug('Connecting to remotecontrold...')
        s.send(PING_PACKET)
        
        ping_reply = s.recv(4096)
        logging.debug('Ping reply: %d bytes' % len(ping_reply))
        parse_ping_reply(ping_reply, keyCodes)
        
        auth_pkt = build_auth_packet(args['client_id'])
        logging.debug('Sending authenticate with MAC %s...' % args['client_id'])
        
        logging.debug('Auth packet: %s' % auth_pkt.hex(' '))
        s.send(auth_pkt)
        
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
    backing_scale = get_backing_scale()

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

    def handle_key_press(key_name: str) -> None:
        """Handle a key press event from mpv, send press+release to device."""
        mpv_key = key_name
        mapped = MPV_KEY_MAP.get(mpv_key)
        if mapped and mapped in keyCodes:
            keycode = keyCodes[mapped]
            logging.debug('Key press: %s -> keycode %d' % (mpv_key, keycode))
            try:
                s.send(keybytes(keycode, 1))
                s.send(keybytes(keycode, 0))
            except socket.error as err:
                logging.error('Error sending key: %s' % err)
        else:
            logging.debug('Unmapped key: %s' % mpv_key)

    setup_global_key_monitor(player, handle_key_press)

    def handle_mouse(p: 'mpv.MPV', x: float, y: float, event_type: int) -> None:
        """Send touch event to device. event_type: 0=press, 1=move, 2=release."""
        try:
            dims = p.osd_dimensions
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
                vw = p.osd_width or 1280
                vh = p.osd_height or 720
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

    @player.key_binding('MBTN_LEFT_DBL')
    @player.key_binding('MBTN_LEFT')
    def mouse_left_handler(state: str = 'p-', name: str = None, char: str = None, *_) -> None:
        nonlocal mouseDown
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
        nonlocal mouseDown
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

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.run()

if __name__ == "__main__":
    main()
