import logging
from typing import Callable, Optional

try:
    from AppKit import NSEvent, NSKeyDown, NSEventMaskKeyDown, NSScreen
except ImportError:
    pass

def get_backing_scale() -> float:
    """Returns the backing scale factor of the main screen."""
    try:
        main_screen = NSScreen.mainScreen()
        if main_screen:
            return main_screen.backingScaleFactor()
    except Exception as e:
        logging.debug(f"Failed to get main screen backing scale: {e}")
    return 1.0

def setup_global_key_monitor(player: 'mpv.MPV', key_handler: Callable[[str], None]) -> None:
    """Sets up a global macOS NSEvent monitor to intercept keys for mpv."""
    
    NS_UP_ARROW = chr(0xF700)
    NS_DOWN_ARROW = chr(0xF701)
    
    from .core import CHAR_TO_MPV, MPV_KEY_MAP

    def global_key_handler(event: 'NSEvent') -> Optional['NSEvent']:
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
                key_handler(mpv_key)
                return None
        return event

    try:
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, global_key_handler)
        logging.debug("Global key monitor registered successfully.")
    except Exception as e:
        logging.error(f"Failed to register Cocoa key monitor: {e}")
