<div align="center">

```
██████╗ ██╗███╗   ██╗ ██████╗  ██████╗██╗  ██╗██╗ ██████╗
██╔══██╗██║████╗  ██║██╔═══██╗██╔════╝██║  ██║██║██╔═══██╗
██████╔╝██║██╔██╗ ██║██║   ██║██║     ███████║██║██║   ██║
██╔═══╝ ██║██║╚██╗██║██║   ██║██║     ██╔══██║██║██║   ██║
██║     ██║██║ ╚████║╚██████╔╝╚██████╗██║  ██║██║╚██████╔╝
╚═╝     ╚═╝╚═╝  ╚═══╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚═╝ ╚═════╝
```

### *The GPIO puppet-master for your Raspberry Pi Zero 2 W.*
**It has no strings — yet it lets you pull all 40 of them.**

*(And unlike its namesake, `status` never lies about a pin.)*

![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20Zero%202%20W-c51a4a?logo=raspberrypi&logoColor=white)
![Python](https://img.shields.io/badge/python-3.7%2B-3776AB?logo=python&logoColor=white)
![Interface](https://img.shields.io/badge/interface-CLI%20%2B%20TUI-informational)
![Architecture](https://img.shields.io/badge/architecture-DDD-blueviolet)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

**PINochIO** is a single-file GPIO controller for the Raspberry Pi Zero 2 W. Flip pins from one-shot shell commands, drive PWM on the hardware-PWM-capable pins, talk over the UART/I²C/SPI alternate functions, or drop into a full-color interactive TUI and conduct the whole 40-pin header like a marionette rig.

## ✨ Features

- 🔌 **One-shot CLI** — `on`, `off`, `toggle`, `read` any BCM pin straight from the shell
- 🌊 **PWM done right** — value defaults to `0` (PWM off); `1–255` sets the duty cycle, and only the genuinely PWM-capable pins (BCM 12, 13, 18, 19) accept it
- 🖥️ **Interactive TUI** — live color-coded table of every pin with keyboard control *and* an embedded `:` command prompt
- 📡 **Alternate functions** — UART send/receive, I²C scan/read/write, SPI transfers
- 🛡️ **Guard rails** — the HAT-EEPROM pins (BCM 0/1) are protected behind `--force`, PWM values are range-checked, non-PWM pins tell you which pins *do* support it
- 🧪 **Mock backend** — runs on any machine without a Pi, so you can rehearse the show before opening night
- 📦 **Zero-install core** — one file, standard library + `RPi.GPIO` (pre-installed on Raspberry Pi OS)

## 📋 Requirements

| Component | Needed for | Install |
|-----------|------------|---------|
| Python 3.7+ | everything | ships with Raspberry Pi OS |
| `RPi.GPIO` | pin control & PWM | pre-installed on Raspberry Pi OS |
| `pyserial` | `serial` commands | `sudo apt install python3-serial` |
| `smbus2` | `i2c` commands | `pip install smbus2` |
| `spidev` | `spi` commands | `pip install spidev` |

Enable the interfaces you plan to use with `sudo raspi-config` → *Interface Options* (Serial Port with login shell **disabled**, I2C, SPI).

## 🚀 Build & Install

There is no build — PINochIO is a single script. Get it onto the Pi and make it executable:

```bash
git clone <this-repo> && cd PINochIO
scp gpioctl.py pi@raspberrypi.local:~/
ssh pi@raspberrypi.local 'chmod +x gpioctl.py'
```

Optional: put it on your `PATH` as `pinochio`:

```bash
sudo cp gpioctl.py /usr/local/bin/pinochio
```

## 🎮 Run

### Pull a string (CLI)

```bash
./gpioctl.py status                    # every pin: mode, level, PWM, functions
./gpioctl.py on 17                     # GPIO17 high
./gpioctl.py off 17                    # GPIO17 low
./gpioctl.py toggle 17
./gpioctl.py read 4 --pull up          # read GPIO4 with internal pull-up
./gpioctl.py all-off                   # curtain call — everything low
```

### PWM (BCM 12, 13, 18, 19)

The value argument **defaults to 0, meaning PWM off**. Anything from 1–255 enables PWM at that duty cycle:

```bash
./gpioctl.py pwm 18            # 0 -> PWM disabled, pin driven low
./gpioctl.py pwm 18 128        # ~50% duty @ 1 kHz
./gpioctl.py pwm 18 255        # full send
```

> **Note:** `RPi.GPIO` PWM is software PWM and lives inside the process, so the `pwm` command holds the terminal until `Ctrl+C` (plain on/off states persist after exit). Want daemon-backed, fire-and-forget PWM? `pigpio` is the roadmap item.

### Alternate functions

```bash
./gpioctl.py serial send "hello" --baud 115200   # UART TXD = GPIO14
./gpioctl.py serial read --seconds 5             # UART RXD = GPIO15
./gpioctl.py i2c scan                            # SDA = GPIO2, SCL = GPIO3
./gpioctl.py i2c read 0x48 0x00
./gpioctl.py i2c write 0x48 0x01 0xFF
./gpioctl.py spi xfer 0x9F 0x00 0x00             # SPI0: GPIO9/10/11, CE0/1 = GPIO8/7
```

### The marionette theatre (TUI)

```bash
./gpioctl.py tui
```

| Key | Action |
|-----|--------|
| `↑` / `↓` (or `j`/`k`) | select a pin |
| `Space` / `Enter` | toggle the selected pin |
| `1` / `0` | pin on / off |
| `+` / `-` | nudge PWM by ±15 |
| `p` | set an exact PWM value (0–255) |
| `r` | read the selected pin as input |
| `:` | open the command prompt — accepts every CLI command (`on 17`, `pwm 18 200`, `serial send hi`, `i2c scan`, …) |
| `q` | quit |

## 🧪 Test

No Pi? No problem. The mock backend simulates the header on any machine (`--mock`, also auto-selected when `RPi.GPIO` is absent):

```bash
python3 gpioctl.py --mock status
python3 gpioctl.py --mock on 17
python3 gpioctl.py --mock pwm 18 128
python3 gpioctl.py --mock pwm 22 100     # correctly rejected: not a PWM pin
python3 gpioctl.py --mock on 0           # correctly rejected: reserved (HAT EEPROM)
python3 gpioctl.py --mock tui            # full TUI rehearsal
```

On real hardware, the classic smoke test — LED + resistor on GPIO17:

```bash
./gpioctl.py on 17 && sleep 1 && ./gpioctl.py off 17
./gpioctl.py pwm 18 64        # gentle glow on GPIO18
```

## 📌 Pin capability map

| BCM | Header | Special functions |
|-----|--------|-------------------|
| 0 / 1 | 27 / 28 | HAT ID EEPROM — **reserved**, needs `--force` |
| 2 / 3 | 3 / 5 | I²C1 SDA / SCL |
| 4 | 7 | GPCLK0, 1-Wire (default) |
| 7–11 | 26/24/21/19/23 | SPI0 (CE1, CE0, MISO, MOSI, SCLK) |
| **12 / 13** | 32 / 33 | **PWM0 / PWM1** |
| 14 / 15 | 8 / 10 | UART TXD / RXD |
| 16 / 17 | 36 / 11 | SPI1 CE2 / CE1 |
| **18 / 19** | 12 / 35 | **PWM0 / PWM1**, SPI1, PCM |
| 20 / 21 | 38 / 40 | SPI1 MOSI / SCLK, PCM |
| 5, 6, 22–27 | — | general-purpose digital I/O |

## 🏛️ Architecture

PINochIO follows **Domain-Driven Design**, collapsed into one deployable file within the `GpioControl` bounded context:

- **Domain** — `Pin` entity, `PinDefinition` value object, `GpioBoard` aggregate root (all invariants — PWM range, PWM capability, reserved pins — enforced here), domain exceptions
- **Application** — `GpioApplicationService` / `BusApplicationService` orchestration, `PinStatusDto`, TUI command interpreter
- **Infrastructure** — `IGpioBackend` port with `RpiGpioBackend` and `MockGpioBackend` adapters; UART/I²C/SPI adapters
- **Presentation** — argparse CLI and the curses TUI

The domain depends on nothing but the `IGpioBackend` abstraction — swap in a `pigpio` or `lgpio` adapter without touching a single business rule.

## 🗺️ Roadmap

- [ ] `pigpio` backend for daemon-persistent, true hardware PWM
- [ ] `watch` command — live-poll an input pin
- [ ] Named pin aliases (`on led-status`)
- [ ] 1-Wire helper on GPIO4

## 🤝 Contributing

PRs welcome. Keep the DDD layering intact (business rules stay in `GpioBoard`), run the mock-backend test commands above before submitting, and remember: every time you bypass the aggregate root, a wooden boy tells a lie.

## 📜 License

Released under the **MIT License** — free to use, no strings attached. *(Sorry.)*
