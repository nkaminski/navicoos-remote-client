# navicoos-remote-client

A native macOS remote display and control client for B&G Vulcan/Zeus marine chartplotter displays (and similar Navico devices).

This application establishes a TCP connection to send control packets (mouse clicks, dragging, hardware key presses) to the MFD, and receives an ultra low-latency RTSP video stream mirroring the display.


## Prerequisites

Ensure you have Poetry installed and the required macOS libraries:

```bash
poetry install
```

*Note: You also need `mpv` installed on your system (e.g., `brew install mpv`).*

## Usage

Connect your Mac to the MFD's Wi-Fi network (or wired network), find the IP address of the MFD, and launch the player:

```bash
poetry run navicoos-remote-client <IP_ADDRESS>
```

Optional arguments:
- `--client-id 00:11:22:33:44:55`: Provide your Mac's MAC address to avoid authorization re-prompts on the MFD.
- `--debug`: Enable verbose packet logging.
- `-c`, `-r`: Override remotecontrol/RTSP ports if necessary.

When connecting to an MFD for the first time, you must tap **Accept** on the physical MFD screen to authorize the connection.

## Keyboard Controls

The MFD's physical hardware buttons are mapped to your Mac's keyboard:

| Mac Key | MFD Hardware Button |
|---------|---------------------|
| `Esc` | Pages |
| `m` | Menu |
| `Up Arrow` | Zoom In |
| `Down Arrow` | Zoom Out |
| `p` | Power |
| `Enter` | Enter |
| `c` | Cancel |
| `o` | MOB |
| `g` | Goto |
| `a` | Mark |
| `w` | WheelKey |
| `q` | *Quit Client* |

---

## Technical Protocol Analysis

`navicoos-remote-client` implements a custom binary TCP protocol on port `6633` over which control packets are exchanged.

### Packet Structure
All packets sent and received follow a strict binary format:
1. **Length** (2 bytes): Total length of the payload
2. **Opcode** (2 bytes): Identifies the action type (e.g., Ping, Auth, Touch)
3. **Payload Data**: Variable length

### Handshake Sequence
1. **Ping Request (`0x0001`)**: The client sends a hardcoded Ping ID (`0x4403D7C3`).
2. **Ping Reply (`0x0002`)**: The MFD responds with its device identity string, software version, display resolution (e.g., 1280x720), and an array of hardware button indices mapped to numerical keycodes.
3. **Auth Request (`0x0003`)**: The client sends its MAC address (6 bytes) and an ASCII string name (null-padded to 32 bytes, e.g., `"iPad"`).
4. **Auth Ack (`0x0004`)**: The MFD echoes back the MAC address and an `0x01` success flag to complete the handshake. (Older versions of the script incorrectly sent additional static packets instead of waiting for this response).

### Interaction Packets
* **Touch Events (`0x1001`)**:
  Includes a 32-bit monotonic timestamp, X/Y coordinates, an event type (`0x00` = press, `0x01` = drag, `0x02` = release), and a touch count (usually `1`).
* **Key Events (`0x1003`)**:
  Includes the numerical keycode (derived dynamically from the Ping Reply) and a press state (`1` = press, `0` = release).

### Video Stream
The device broadcasts the screen mirror via an RTSP video stream located at `rtsp://<IP_ADDRESS>:554/screenmirror`. The client consumes this via `libmpv` configured for ultra-low latency.

## Acknowledgements

This project is a native macOS Cocoa rewrite and continuation based on the original [BanGPlayer](https://github.com/htool/BanGPlayer) by [htool](https://github.com/htool).

## License

This project is licensed under the MIT License.
