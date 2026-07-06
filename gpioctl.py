#!/usr/bin/env python3
"""
gpioctl — Raspberry Pi Zero 2 W GPIO controller (CLI + interactive TUI).

Bounded Context: GpioControl
Layers (DDD, collapsed into one deployable file):
    [domain]         Pin entity, PinDefinition value object, GpioBoard aggregate root,
                     domain exceptions
    [application]    GpioApplicationService, BusApplicationService, command interpreter
    [infrastructure] RpiGpioBackend / MockGpioBackend, SerialPortAdapter,
                     I2cBusAdapter, SpiBusAdapter
    [presentation]   argparse CLI, curses TUI

Usage (CLI):
    gpioctl.py status                     # table of every pin + capabilities
    gpioctl.py on 17                      # drive BCM17 high
    gpioctl.py off 17                     # drive BCM17 low
    gpioctl.py toggle 17
    gpioctl.py read 4 --pull up           # read BCM4 with internal pull-up
    gpioctl.py pwm 18                     # value defaults to 0 -> PWM disabled
    gpioctl.py pwm 18 128                 # 50% duty on a PWM-capable pin (0-255)
    gpioctl.py serial send "hello" --baud 115200
    gpioctl.py serial read --seconds 5
    gpioctl.py i2c scan
    gpioctl.py i2c read 0x48 0x00
    gpioctl.py i2c write 0x48 0x01 0xFF
    gpioctl.py spi xfer 0x9F 0x00 0x00
    gpioctl.py all-off
    gpioctl.py tui                        # interactive TUI

Usage (Python import — drop gpioctl.py next to your script or on PYTHONPATH):
    import gpioctl
    gpioctl.on(17)                        # -> 1 (new level)
    gpioctl.off(17)                       # -> 0
    gpioctl.pwm(18, 128)                  # PWM while your process runs; 0 disables
    gpioctl.read(4, pull="up")            # -> 0 or 1
    gpioctl.serial_send("hello")          # -> bytes written
    gpioctl.usage("pwm")                  # topic help ('usage', so it never
                                          #  shadows Python's built-in help())

Help (topic-based, same topics behind all three doors):
    gpioctl.py help                       # overview + topic list
    gpioctl.py help pwm                   # zero in on one command
    gpioctl.usage("serial")               # from Python
    :help i2c                             # from the TUI prompt

Notes:
    * Software PWM (RPi.GPIO) lives inside this process. The `pwm` CLI command
      therefore holds the terminal until Ctrl+C; in the TUI, PWM persists while
      the TUI is open. Plain on/off states survive after the process exits.
    * BCM0/BCM1 (ID EEPROM) are reserved; pass --force to touch them.
    * Off the Pi (no RPi.GPIO) the script drops into a mock backend so the TUI
      and commands can be exercised anywhere.
"""

import argparse
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple

PWM_FREQUENCY_HZ = 1000
DEFAULT_SERIAL_PORT = "/dev/serial0"
DEFAULT_BAUD = 115200


# ============================================================================
# [domain] exceptions
# ============================================================================

class GpioDomainException(Exception):
    """Base exception for the GpioControl bounded context."""


class PinNotFoundException(GpioDomainException):
    def __init__(self, bcm: int):
        super().__init__(f"BCM{bcm} is not a user-accessible GPIO on the Pi Zero 2 W header.")


class PinReservedException(GpioDomainException):
    def __init__(self, bcm: int, reason: str):
        super().__init__(f"BCM{bcm} is reserved ({reason}). Use --force to override.")


class PwmNotSupportedException(GpioDomainException):
    def __init__(self, bcm: int, pwm_pins: List[int]):
        super().__init__(
            f"BCM{bcm} has no hardware PWM channel. "
            f"PWM-capable pins: {', '.join('BCM%d' % p for p in pwm_pins)}."
        )


class InvalidPwmValueException(GpioDomainException):
    def __init__(self, value):
        super().__init__(f"PWM value must be an integer 0-255 (got {value!r}).")


class BusUnavailableException(GpioDomainException):
    pass


# ============================================================================
# [domain] value objects
# ============================================================================

@dataclass(frozen=True)
class PinDefinition:
    """Value Object — immutable description of one BCM line on the 40-pin header."""
    bcm: int
    header: int                       # physical pin number on the 40-pin header
    alt_functions: FrozenSet[str] = frozenset()
    hardware_pwm: bool = False
    pwm_channel: Optional[int] = None
    reserved: Optional[str] = None    # reason, if the pin should not be touched

    @property
    def label(self) -> str:
        return f"GPIO{self.bcm}"

    @property
    def capability_summary(self) -> str:
        caps = sorted(self.alt_functions)
        if self.hardware_pwm:
            caps.insert(0, f"PWM{self.pwm_channel}")
        return ", ".join(caps) if caps else "digital"


def _defn(bcm, header, alts=(), pwm_ch=None, reserved=None) -> PinDefinition:
    return PinDefinition(
        bcm=bcm, header=header, alt_functions=frozenset(alts),
        hardware_pwm=pwm_ch is not None, pwm_channel=pwm_ch, reserved=reserved,
    )


# Raspberry Pi Zero 2 W — BCM lines exposed on the 40-pin header.
PIN_TABLE: Dict[int, PinDefinition] = {d.bcm: d for d in [
    _defn(0,  27, ["ID_SD (EEPROM)"], reserved="HAT ID EEPROM data"),
    _defn(1,  28, ["ID_SC (EEPROM)"], reserved="HAT ID EEPROM clock"),
    _defn(2,  3,  ["I2C1_SDA"]),
    _defn(3,  5,  ["I2C1_SCL"]),
    _defn(4,  7,  ["GPCLK0", "1-Wire (default)"]),
    _defn(5,  29, ["GPCLK1"]),
    _defn(6,  31, ["GPCLK2"]),
    _defn(7,  26, ["SPI0_CE1"]),
    _defn(8,  24, ["SPI0_CE0"]),
    _defn(9,  21, ["SPI0_MISO"]),
    _defn(10, 19, ["SPI0_MOSI"]),
    _defn(11, 23, ["SPI0_SCLK"]),
    _defn(12, 32, [], pwm_ch=0),
    _defn(13, 33, [], pwm_ch=1),
    _defn(14, 8,  ["UART_TXD"]),
    _defn(15, 10, ["UART_RXD"]),
    _defn(16, 36, ["SPI1_CE2"]),
    _defn(17, 11, ["SPI1_CE1"]),
    _defn(18, 12, ["SPI1_CE0", "PCM_CLK"], pwm_ch=0),
    _defn(19, 35, ["SPI1_MISO", "PCM_FS"], pwm_ch=1),
    _defn(20, 38, ["SPI1_MOSI", "PCM_DIN"]),
    _defn(21, 40, ["SPI1_SCLK", "PCM_DOUT"]),
    _defn(22, 15, []),
    _defn(23, 16, []),
    _defn(24, 18, []),
    _defn(25, 22, []),
    _defn(26, 37, []),
    _defn(27, 13, []),
]}

PWM_CAPABLE_PINS: List[int] = sorted(b for b, d in PIN_TABLE.items() if d.hardware_pwm)
UART_PINS: List[int] = [14, 15]


class PinMode(Enum):
    UNSET = "unset"
    OUTPUT = "out"
    INPUT = "in"
    PWM = "pwm"


# ============================================================================
# [domain] entities & aggregate
# ============================================================================

@dataclass
class Pin:
    """Entity — identified by its BCM number; state changes only via methods."""
    definition: PinDefinition
    mode: PinMode = PinMode.UNSET
    level: int = 0                    # last driven/read logic level
    pwm_value: int = 0                # 0-255; 0 means PWM disabled

    @property
    def bcm(self) -> int:
        return self.definition.bcm

    def guard_accessible(self, force: bool) -> None:
        if self.definition.reserved and not force:
            raise PinReservedException(self.bcm, self.definition.reserved)

    def guard_pwm_capable(self) -> None:
        if not self.definition.hardware_pwm:
            raise PwmNotSupportedException(self.bcm, PWM_CAPABLE_PINS)

    @staticmethod
    def guard_pwm_value(value: int) -> None:
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise InvalidPwmValueException(value)


class GpioBoard:
    """Aggregate Root — the only entry point for mutating pin state.

    Enforces the invariants: reserved pins need force, PWM only on
    PWM-capable pins, PWM values within 0-255.
    """

    def __init__(self, backend: "IGpioBackend"):
        self._backend = backend
        self._pins: Dict[int, Pin] = {bcm: Pin(defn) for bcm, defn in PIN_TABLE.items()}

    # -- queries -------------------------------------------------------------

    def pin(self, bcm: int) -> Pin:
        if bcm not in self._pins:
            raise PinNotFoundException(bcm)
        return self._pins[bcm]

    def all_pins(self) -> List[Pin]:
        return [self._pins[b] for b in sorted(self._pins)]

    # -- commands ------------------------------------------------------------

    def switch_on(self, bcm: int, force: bool = False) -> Pin:
        pin = self.pin(bcm)
        pin.guard_accessible(force)
        self._stop_pwm_if_running(pin)
        self._backend.setup_output(bcm)
        self._backend.write(bcm, 1)
        pin.mode, pin.level = PinMode.OUTPUT, 1
        return pin

    def switch_off(self, bcm: int, force: bool = False) -> Pin:
        pin = self.pin(bcm)
        pin.guard_accessible(force)
        self._stop_pwm_if_running(pin)
        self._backend.setup_output(bcm)
        self._backend.write(bcm, 0)
        pin.mode, pin.level = PinMode.OUTPUT, 0
        return pin

    def toggle(self, bcm: int, force: bool = False) -> Pin:
        pin = self.pin(bcm)
        if pin.mode == PinMode.OUTPUT and pin.level == 1:
            return self.switch_off(bcm, force)
        return self.switch_on(bcm, force)

    def read(self, bcm: int, pull: str = "none", force: bool = False) -> int:
        pin = self.pin(bcm)
        pin.guard_accessible(force)
        self._stop_pwm_if_running(pin)
        self._backend.setup_input(bcm, pull)
        pin.mode = PinMode.INPUT
        pin.level = self._backend.read(bcm)
        return pin.level

    def set_pwm(self, bcm: int, value: int = 0, force: bool = False) -> Pin:
        """value defaults to 0 => PWM disabled; 1-255 => duty cycle."""
        pin = self.pin(bcm)
        pin.guard_accessible(force)
        pin.guard_pwm_capable()
        Pin.guard_pwm_value(value)
        if value == 0:
            self._stop_pwm_if_running(pin)
            self._backend.setup_output(bcm)
            self._backend.write(bcm, 0)
            pin.mode, pin.level, pin.pwm_value = PinMode.OUTPUT, 0, 0
        else:
            duty = value / 255 * 100
            if pin.mode == PinMode.PWM:
                self._backend.pwm_set_duty(bcm, duty)
            else:
                self._backend.setup_output(bcm)
                self._backend.pwm_start(bcm, PWM_FREQUENCY_HZ, duty)
            pin.mode, pin.pwm_value = PinMode.PWM, value
            pin.level = 1
        return pin

    def all_off(self, force: bool = False) -> List[int]:
        touched = []
        for pin in self.all_pins():
            if pin.definition.reserved and not force:
                continue
            if pin.mode in (PinMode.OUTPUT, PinMode.PWM):
                self.switch_off(pin.bcm, force)
                touched.append(pin.bcm)
        return touched

    def release(self) -> None:
        for pin in self.all_pins():
            self._stop_pwm_if_running(pin)

    def _stop_pwm_if_running(self, pin: Pin) -> None:
        if pin.mode == PinMode.PWM:
            self._backend.pwm_stop(pin.bcm)
            pin.pwm_value = 0


# ============================================================================
# [infrastructure] GPIO backends
# ============================================================================

class IGpioBackend:
    """Port — contract the domain depends on; implementations live below."""

    name = "abstract"

    def setup_output(self, bcm: int) -> None: raise NotImplementedError
    def setup_input(self, bcm: int, pull: str) -> None: raise NotImplementedError
    def write(self, bcm: int, level: int) -> None: raise NotImplementedError
    def read(self, bcm: int) -> int: raise NotImplementedError
    def pwm_start(self, bcm: int, freq: int, duty: float) -> None: raise NotImplementedError
    def pwm_set_duty(self, bcm: int, duty: float) -> None: raise NotImplementedError
    def pwm_stop(self, bcm: int) -> None: raise NotImplementedError


class RpiGpioBackend(IGpioBackend):
    """Adapter over RPi.GPIO (software PWM on the hardware-PWM-capable pins)."""

    name = "RPi.GPIO"

    def __init__(self):
        import RPi.GPIO as GPIO  # noqa: N814
        self._gpio = GPIO
        self._pwm = {}
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    def setup_output(self, bcm):
        self._gpio.setup(bcm, self._gpio.OUT)

    def setup_input(self, bcm, pull):
        pud = {"up": self._gpio.PUD_UP, "down": self._gpio.PUD_DOWN}.get(pull, self._gpio.PUD_OFF)
        self._gpio.setup(bcm, self._gpio.IN, pull_up_down=pud)

    def write(self, bcm, level):
        self._gpio.output(bcm, self._gpio.HIGH if level else self._gpio.LOW)

    def read(self, bcm):
        return int(self._gpio.input(bcm))

    def pwm_start(self, bcm, freq, duty):
        p = self._gpio.PWM(bcm, freq)
        p.start(duty)
        self._pwm[bcm] = p

    def pwm_set_duty(self, bcm, duty):
        self._pwm[bcm].ChangeDutyCycle(duty)

    def pwm_stop(self, bcm):
        p = self._pwm.pop(bcm, None)
        if p:
            p.stop()


class MockGpioBackend(IGpioBackend):
    """In-memory stand-in so the tool runs on non-Pi machines for development."""

    name = "MOCK (not a Raspberry Pi)"

    def __init__(self):
        self._levels: Dict[int, int] = {}

    def setup_output(self, bcm): self._levels.setdefault(bcm, 0)
    def setup_input(self, bcm, pull): self._levels[bcm] = 1 if pull == "up" else 0
    def write(self, bcm, level): self._levels[bcm] = level
    def read(self, bcm): return self._levels.get(bcm, 0)
    def pwm_start(self, bcm, freq, duty): self._levels[bcm] = 1
    def pwm_set_duty(self, bcm, duty): pass
    def pwm_stop(self, bcm): self._levels[bcm] = 0


def create_backend(force_mock: bool = False) -> IGpioBackend:
    if force_mock:
        return MockGpioBackend()
    try:
        return RpiGpioBackend()
    except (ImportError, RuntimeError):
        print("[gpioctl] RPi.GPIO unavailable — running with MOCK backend.", file=sys.stderr)
        return MockGpioBackend()


# ============================================================================
# [infrastructure] bus adapters (UART / I2C / SPI)
# ============================================================================

class SerialPortAdapter:
    """UART on BCM14 (TXD) / BCM15 (RXD) via /dev/serial0."""

    def __init__(self, port: str = DEFAULT_SERIAL_PORT, baud: int = DEFAULT_BAUD):
        try:
            import serial
        except ImportError:
            raise BusUnavailableException(
                "pyserial is not installed. Run: sudo apt install python3-serial "
                "(or pip install pyserial)."
            )
        try:
            self._port = serial.Serial(port, baudrate=baud, timeout=1)
        except Exception as exc:
            raise BusUnavailableException(
                f"Cannot open {port}: {exc}. Enable the UART with 'sudo raspi-config' "
                "(Interface Options -> Serial Port, disable login shell, enable hardware)."
            )

    def send(self, text: str, newline: bool = True) -> int:
        data = (text + ("\n" if newline else "")).encode()
        return self._port.write(data)

    def read_for(self, seconds: float) -> bytes:
        deadline = time.monotonic() + seconds
        chunks = []
        while time.monotonic() < deadline:
            chunk = self._port.read(256)
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def close(self):
        self._port.close()


class I2cBusAdapter:
    """I2C1 on BCM2 (SDA) / BCM3 (SCL)."""

    def __init__(self, bus: int = 1):
        try:
            from smbus2 import SMBus
        except ImportError:
            raise BusUnavailableException(
                "smbus2 is not installed. Run: pip install smbus2 "
                "(and enable I2C via 'sudo raspi-config')."
            )
        try:
            self._bus = SMBus(bus)
        except Exception as exc:
            raise BusUnavailableException(f"Cannot open I2C bus {bus}: {exc}")

    def scan(self) -> List[int]:
        found = []
        for addr in range(0x03, 0x78):
            try:
                self._bus.write_quick(addr)
                found.append(addr)
            except OSError:
                pass
        return found

    def read_register(self, addr: int, reg: int) -> int:
        return self._bus.read_byte_data(addr, reg)

    def write_register(self, addr: int, reg: int, value: int) -> None:
        self._bus.write_byte_data(addr, reg, value)

    def close(self):
        self._bus.close()


class SpiBusAdapter:
    """SPI0 on BCM9/10/11 with CE0=BCM8, CE1=BCM7."""

    def __init__(self, bus: int = 0, device: int = 0, speed_hz: int = 500000):
        try:
            import spidev
        except ImportError:
            raise BusUnavailableException(
                "spidev is not installed. Run: pip install spidev "
                "(and enable SPI via 'sudo raspi-config')."
            )
        try:
            self._spi = spidev.SpiDev()
            self._spi.open(bus, device)
            self._spi.max_speed_hz = speed_hz
        except Exception as exc:
            raise BusUnavailableException(f"Cannot open SPI {bus}.{device}: {exc}")

    def transfer(self, data: List[int]) -> List[int]:
        return self._spi.xfer2(list(data))

    def close(self):
        self._spi.close()


# ============================================================================
# [application] services
# ============================================================================

@dataclass
class PinStatusDto:
    bcm: int
    header: int
    mode: str
    level: int
    pwm_value: int
    capabilities: str
    reserved: Optional[str]


class GpioApplicationService:
    """Orchestrates pin use cases against the GpioBoard aggregate."""

    def __init__(self, board: GpioBoard):
        self._board = board

    def turn_on(self, bcm: int, force=False) -> str:
        self._board.switch_on(bcm, force)
        return f"GPIO{bcm} -> ON (high)"

    def turn_off(self, bcm: int, force=False) -> str:
        self._board.switch_off(bcm, force)
        return f"GPIO{bcm} -> OFF (low)"

    def toggle(self, bcm: int, force=False) -> str:
        pin = self._board.toggle(bcm, force)
        return f"GPIO{bcm} -> {'ON' if pin.level else 'OFF'}"

    def read(self, bcm: int, pull="none", force=False) -> str:
        level = self._board.read(bcm, pull, force)
        return f"GPIO{bcm} reads {level} ({'HIGH' if level else 'LOW'}, pull={pull})"

    def set_pwm(self, bcm: int, value: int = 0, force=False) -> str:
        pin = self._board.set_pwm(bcm, value, force)
        if value == 0:
            return f"GPIO{bcm} PWM disabled (pin low)"
        return f"GPIO{bcm} PWM {value}/255 ({value / 255 * 100:.1f}% duty @ {PWM_FREQUENCY_HZ} Hz)"

    def all_off(self, force=False) -> str:
        touched = self._board.all_off(force)
        return f"Switched off: {', '.join('GPIO%d' % b for b in touched) or 'nothing active'}"

    def status(self) -> List[PinStatusDto]:
        return [
            PinStatusDto(
                bcm=p.bcm, header=p.definition.header, mode=p.mode.value,
                level=p.level, pwm_value=p.pwm_value,
                capabilities=p.definition.capability_summary,
                reserved=p.definition.reserved,
            )
            for p in self._board.all_pins()
        ]

    def release(self):
        self._board.release()


class BusApplicationService:
    """Orchestrates UART / I2C / SPI use cases (the pins' alternate functions)."""

    def serial_send(self, text, port=DEFAULT_SERIAL_PORT, baud=DEFAULT_BAUD) -> str:
        uart = SerialPortAdapter(port, baud)
        try:
            n = uart.send(text)
            return f"Sent {n} bytes on {port} @ {baud} baud (TXD=GPIO14)"
        finally:
            uart.close()

    def serial_read(self, seconds=3.0, port=DEFAULT_SERIAL_PORT, baud=DEFAULT_BAUD) -> str:
        uart = SerialPortAdapter(port, baud)
        try:
            data = uart.read_for(seconds)
            if not data:
                return f"No data on {port} within {seconds}s (RXD=GPIO15)"
            return f"Received {len(data)} bytes: {data!r}"
        finally:
            uart.close()

    def i2c_scan(self) -> str:
        bus = I2cBusAdapter()
        try:
            found = bus.scan()
            if not found:
                return "I2C scan: no devices found (SDA=GPIO2, SCL=GPIO3)"
            return "I2C devices: " + ", ".join(f"0x{a:02X}" for a in found)
        finally:
            bus.close()

    def i2c_read(self, addr: int, reg: int) -> str:
        bus = I2cBusAdapter()
        try:
            val = bus.read_register(addr, reg)
            return f"I2C 0x{addr:02X} reg 0x{reg:02X} = 0x{val:02X} ({val})"
        finally:
            bus.close()

    def i2c_write(self, addr: int, reg: int, value: int) -> str:
        bus = I2cBusAdapter()
        try:
            bus.write_register(addr, reg, value)
            return f"I2C wrote 0x{value:02X} to 0x{addr:02X} reg 0x{reg:02X}"
        finally:
            bus.close()

    def spi_transfer(self, data: List[int], bus=0, device=0, speed=500000) -> str:
        spi = SpiBusAdapter(bus, device, speed)
        try:
            rx = spi.transfer(data)
            return "SPI rx: " + " ".join(f"0x{b:02X}" for b in rx)
        finally:
            spi.close()


class CommandInterpreter:
    """Parses TUI prompt commands and dispatches to the application services."""

    HELP = (
        "on <pin> | off <pin> | toggle <pin> | read <pin> [up|down] | "
        "pwm <pin> [0-255] | serial send <text> | serial read [secs] | "
        "i2c scan | i2c read <addr> <reg> | i2c write <addr> <reg> <val> | "
        "spi xfer <b0> <b1> ... | alloff | help [topic] | quit"
    )

    def __init__(self, gpio: GpioApplicationService, bus: BusApplicationService):
        self._gpio = gpio
        self._bus = bus

    def execute(self, line: str) -> Tuple[str, bool]:
        """Returns (message, should_quit)."""
        parts = line.strip().split()
        if not parts:
            return "", False
        cmd = parts[0].lower()
        try:
            if cmd in ("quit", "q", "exit"):
                return "bye", True
            if cmd in ("help", "h", "?"):
                if len(parts) > 1:
                    return usage_text(parts[1]), False   # multi-line -> TUI overlay
                return self.HELP, False
            if cmd == "on":
                return self._gpio.turn_on(int(parts[1])), False
            if cmd == "off":
                return self._gpio.turn_off(int(parts[1])), False
            if cmd in ("toggle", "t"):
                return self._gpio.toggle(int(parts[1])), False
            if cmd == "read":
                pull = parts[2] if len(parts) > 2 else "none"
                return self._gpio.read(int(parts[1]), pull), False
            if cmd == "pwm":
                value = int(parts[2]) if len(parts) > 2 else 0   # default 0 = no PWM
                return self._gpio.set_pwm(int(parts[1]), value), False
            if cmd == "alloff":
                return self._gpio.all_off(), False
            if cmd == "serial":
                if parts[1] == "send":
                    return self._bus.serial_send(" ".join(parts[2:])), False
                if parts[1] == "read":
                    secs = float(parts[2]) if len(parts) > 2 else 3.0
                    return self._bus.serial_read(secs), False
            if cmd == "i2c":
                if parts[1] == "scan":
                    return self._bus.i2c_scan(), False
                if parts[1] == "read":
                    return self._bus.i2c_read(int(parts[2], 0), int(parts[3], 0)), False
                if parts[1] == "write":
                    return self._bus.i2c_write(int(parts[2], 0), int(parts[3], 0),
                                               int(parts[4], 0)), False
            if cmd == "spi" and parts[1] == "xfer":
                return self._bus.spi_transfer([int(b, 0) for b in parts[2:]]), False
            return f"Unknown command: {line!r}  (type 'help')", False
        except GpioDomainException as exc:
            return f"! {exc}", False
        except (IndexError, ValueError):
            return f"Bad arguments for {cmd!r}  (type 'help')", False


# ============================================================================
# [application] topic help — usage() / usage_text()
# ============================================================================

def _pins_help() -> str:
    lines = ["pins — Raspberry Pi Zero 2 W BCM lines on the 40-pin header", ""]
    for d in (PIN_TABLE[b] for b in sorted(PIN_TABLE)):
        extra = f"  [RESERVED: {d.reserved}]" if d.reserved else ""
        lines.append(f"  GPIO{d.bcm:<3} hdr {d.header:<3} {d.capability_summary}{extra}")
    lines += ["", "PWM-capable: " + ", ".join(f"BCM{b}" for b in PWM_CAPABLE_PINS),
              "UART: BCM14/15   I2C1: BCM2/3   SPI0: BCM7-11"]
    return "\n".join(lines)


HELP_TOPICS: Dict[str, str] = {
    "overview": """\
PINochIO (gpioctl) — Raspberry Pi Zero 2 W GPIO controller.

Three ways to pull the strings:
  1. Command line : python3 gpioctl.py on 17
  2. Python import: import gpioctl; gpioctl.on(17)
  3. Interactive  : python3 gpioctl.py tui   (press ':' for the prompt)

Zero in on any topic:
  CLI   : python3 gpioctl.py help <topic>
  Python: gpioctl.usage("<topic>")
  TUI   : :help <topic>

Topics: on, off, toggle, read, pwm, status, all-off, serial, i2c, spi,
        pins, tui, import, help""",

    "on": """\
on / off / toggle — drive a pin as a digital output

CLI   : python3 gpioctl.py on 17
        python3 gpioctl.py off 17
        python3 gpioctl.py toggle 17
Python: import gpioctl
        gpioctl.on(17)        # -> 1 (new level)
        gpioctl.off(17)       # -> 0
        gpioctl.toggle(17)    # -> new level
TUI   : arrows select a pin, then SPACE (toggle), '1' (on), '0' (off);
        or from the ':' prompt: on 17
Notes : pins are BCM numbers. Levels persist after the CLI exits.
        BCM0/1 are reserved (HAT EEPROM) — needs --force / force=True.""",

    "read": """\
read — sample a pin as a digital input

CLI   : python3 gpioctl.py read 4 --pull up      # pull: up | down | none
Python: gpioctl.read(4, pull="up")    # -> 0 or 1
TUI   : select the pin and press 'r'; or from the ':' prompt: read 4 up
Notes : reading reconfigures the pin as an input (any PWM on it stops).""",

    "pwm": f"""\
pwm — pulse-width modulation on the PWM-capable pins
      ({', '.join('BCM%d' % b for b in PWM_CAPABLE_PINS)})

The value defaults to 0 (PWM disabled); 1-255 sets the duty cycle
at {PWM_FREQUENCY_HZ} Hz.

CLI   : python3 gpioctl.py pwm 18 128    # ~50% duty
        python3 gpioctl.py pwm 18        # 0 -> PWM off, pin driven low
        (software PWM lives in the process: the CLI holds until Ctrl+C)
Python: gpioctl.pwm(18, 128)             # runs while your process lives
        gpioctl.pwm(18)                  # off
TUI   : select the pin, then +/- to nudge by 15 or 'p' for an exact
        value; or from the ':' prompt: pwm 18 128
Errors: non-PWM pins -> PwmNotSupportedException; outside 0-255 ->
        InvalidPwmValueException.""",

    "status": """\
status — table of every pin: mode, level, PWM value, special functions

CLI   : python3 gpioctl.py status
Python: for p in gpioctl.status():       # list of PinStatusDto
            print(p.bcm, p.mode, p.level, p.pwm_value)
TUI   : the main screen *is* a live status table.""",

    "all-off": """\
all-off — switch every active output/PWM pin low (curtain call)

CLI   : python3 gpioctl.py all-off
Python: gpioctl.all_off()     # -> list of BCM numbers switched off
TUI   : from the ':' prompt: alloff""",

    "serial": """\
serial — UART on GPIO14 (TXD) / GPIO15 (RXD), default port /dev/serial0

CLI   : python3 gpioctl.py serial send "hello" --baud 115200
        python3 gpioctl.py serial read --seconds 5
Python: gpioctl.serial_send("hello", baud=115200)   # -> bytes written
        gpioctl.serial_read(seconds=5)              # -> bytes received
TUI   : from the ':' prompt: serial send hello  |  serial read 5
Needs : pyserial; enable the UART via 'sudo raspi-config'
        (Interface Options -> Serial Port: login shell OFF, port ON).""",

    "i2c": """\
i2c — I2C1 bus on GPIO2 (SDA) / GPIO3 (SCL)

CLI   : python3 gpioctl.py i2c scan
        python3 gpioctl.py i2c read 0x48 0x00
        python3 gpioctl.py i2c write 0x48 0x01 0xFF
Python: gpioctl.i2c_scan()                 # -> [72, ...] (int addresses)
        gpioctl.i2c_read(0x48, 0x00)       # -> int
        gpioctl.i2c_write(0x48, 0x01, 0xFF)
TUI   : from the ':' prompt: i2c scan | i2c read 0x48 0x00 | i2c write ...
Needs : smbus2; enable I2C via 'sudo raspi-config'.""",

    "spi": """\
spi — SPI0 on GPIO9 (MISO) / GPIO10 (MOSI) / GPIO11 (SCLK),
      CE0 = GPIO8, CE1 = GPIO7

CLI   : python3 gpioctl.py spi xfer 0x9F 0x00 0x00 \\
            [--bus 0 --device 0 --speed 500000]
Python: gpioctl.spi_xfer([0x9F, 0x00, 0x00])   # -> received bytes (list)
TUI   : from the ':' prompt: spi xfer 0x9F 0x00 0x00
Needs : spidev; enable SPI via 'sudo raspi-config'.""",

    "pins": "",   # filled in below (built from PIN_TABLE)

    "tui": """\
tui — the interactive marionette theatre

CLI   : python3 gpioctl.py tui        (add --mock off the Pi)
Keys  : arrows/j/k select | SPACE toggle | 1 on | 0 off | +/- PWM +-15
        p exact PWM | r read | : command prompt | q quit
Prompt: ':' accepts every CLI command, plus 'help <topic>' — topic help
        opens an overlay (press any key to close it).""",

    "import": """\
import — use PINochIO as a library in your own project

    import gpioctl              # gpioctl.py next to your script/PYTHONPATH

    gpioctl.on(17)                       # -> 1
    gpioctl.pwm(18, 128)                 # PWM while your process runs
    level = gpioctl.read(4, pull="up")   # -> 0 or 1
    gpioctl.serial_send("hi")
    gpioctl.all_off()
    gpioctl.release()                    # stop PWM threads on shutdown

Advanced:
    gpioctl.configure(mock=True)         # force the simulator (call first)
    board = gpioctl.GpioBoard(gpioctl.create_backend())   # own aggregate
Errors: functions raise gpioctl.GpioDomainException subclasses
        (PwmNotSupportedException, PinReservedException, ...).
Help  : gpioctl.usage() / gpioctl.usage("pwm") — named 'usage' so it
        never shadows Python's built-in help().""",

    "help": """\
help — topic help, three doors in

CLI   : python3 gpioctl.py help [topic]
Python: gpioctl.usage("[topic]")         # or usage_text() for the string
TUI   : :help [topic]

Topics: on, off, toggle, read, pwm, status, all-off, serial, i2c, spi,
        pins, tui, import, help""",
}
HELP_TOPICS["pins"] = _pins_help()

_TOPIC_ALIASES = {
    "off": "on", "toggle": "on", "switch": "on",
    "alloff": "all-off", "all_off": "all-off",
    "uart": "serial", "module": "import", "scripting": "import",
    "library": "import", "api": "import", "usage": "help",
    "pin": "pins", "gpio": "pins", "interactive": "tui",
}


def usage_text(topic: Optional[str] = None) -> str:
    """Return the help text for a topic (None -> overview)."""
    if topic is None:
        return HELP_TOPICS["overview"]
    key = topic.lower().strip()
    key = _TOPIC_ALIASES.get(key, key)
    if key not in HELP_TOPICS:
        return (f"Unknown help topic {topic!r}.\n\n"
                f"Topics: {', '.join(sorted(HELP_TOPICS))}")
    return HELP_TOPICS[key]


def usage(topic: Optional[str] = None) -> None:
    """Print topic help. Named 'usage' so it never shadows built-in help()."""
    print(usage_text(topic))


# ============================================================================
# [public API] import gpioctl — call the strings directly from your code
# ============================================================================

__all__ = [
    "on", "off", "toggle", "read", "pwm", "status", "all_off", "release",
    "configure", "usage", "usage_text",
    "serial_send", "serial_read", "i2c_scan", "i2c_read", "i2c_write", "spi_xfer",
    "GpioBoard", "GpioApplicationService", "BusApplicationService",
    "create_backend", "PIN_TABLE", "PWM_CAPABLE_PINS",
    "GpioDomainException", "PinNotFoundException", "PinReservedException",
    "PwmNotSupportedException", "InvalidPwmValueException",
    "BusUnavailableException",
]

_module_board: Optional[GpioBoard] = None
_module_mock: bool = False


def configure(mock: bool = False) -> None:
    """Choose the backend for the module-level functions (default: auto-detect).

    Call before the first pin function; calling later resets the board.
    """
    global _module_board, _module_mock
    if _module_board is not None:
        _module_board.release()
    _module_board = None
    _module_mock = mock


def _board() -> GpioBoard:
    global _module_board
    if _module_board is None:
        _module_board = GpioBoard(create_backend(_module_mock))
    return _module_board


def on(pin: int, force: bool = False) -> int:
    """Drive a pin high. Returns the new level (1)."""
    return _board().switch_on(pin, force).level


def off(pin: int, force: bool = False) -> int:
    """Drive a pin low. Returns the new level (0)."""
    return _board().switch_off(pin, force).level


def toggle(pin: int, force: bool = False) -> int:
    """Toggle a pin. Returns the new level."""
    return _board().toggle(pin, force).level


def read(pin: int, pull: str = "none", force: bool = False) -> int:
    """Read a pin as an input ('up'/'down'/'none' pull). Returns 0 or 1."""
    return _board().read(pin, pull, force)


def pwm(pin: int, value: int = 0, force: bool = False) -> int:
    """Set PWM on a PWM-capable pin. value 0 (default) disables; 1-255 = duty.

    Software PWM runs only while your process lives. Returns the PWM value.
    """
    return _board().set_pwm(pin, value, force).pwm_value


def all_off(force: bool = False) -> List[int]:
    """Switch every active output/PWM pin low. Returns the BCM numbers touched."""
    return _board().all_off(force)


def status() -> List[PinStatusDto]:
    """Snapshot of every pin as PinStatusDto objects."""
    return GpioApplicationService(_board()).status()


def release() -> None:
    """Stop any running PWM threads (call on shutdown)."""
    if _module_board is not None:
        _module_board.release()


def serial_send(text: str, port: str = DEFAULT_SERIAL_PORT,
                baud: int = DEFAULT_BAUD, newline: bool = True) -> int:
    """Send text over the UART (TXD=GPIO14). Returns bytes written."""
    uart = SerialPortAdapter(port, baud)
    try:
        return uart.send(text, newline)
    finally:
        uart.close()


def serial_read(seconds: float = 3.0, port: str = DEFAULT_SERIAL_PORT,
                baud: int = DEFAULT_BAUD) -> bytes:
    """Read from the UART (RXD=GPIO15) for `seconds`. Returns raw bytes."""
    uart = SerialPortAdapter(port, baud)
    try:
        return uart.read_for(seconds)
    finally:
        uart.close()


def i2c_scan(bus: int = 1) -> List[int]:
    """Scan the I2C bus (SDA=GPIO2, SCL=GPIO3). Returns found addresses."""
    adapter = I2cBusAdapter(bus)
    try:
        return adapter.scan()
    finally:
        adapter.close()


def i2c_read(addr: int, reg: int, bus: int = 1) -> int:
    """Read one register byte from an I2C device."""
    adapter = I2cBusAdapter(bus)
    try:
        return adapter.read_register(addr, reg)
    finally:
        adapter.close()


def i2c_write(addr: int, reg: int, value: int, bus: int = 1) -> None:
    """Write one register byte to an I2C device."""
    adapter = I2cBusAdapter(bus)
    try:
        adapter.write_register(addr, reg, value)
    finally:
        adapter.close()


def spi_xfer(data: List[int], bus: int = 0, device: int = 0,
             speed: int = 500000) -> List[int]:
    """Full-duplex SPI transfer on SPI0. Returns the received bytes."""
    adapter = SpiBusAdapter(bus, device, speed)
    try:
        return adapter.transfer(data)
    finally:
        adapter.close()


# ============================================================================
# [presentation] TUI
# ============================================================================

TUI_KEY_HELP = (
    " ↑/↓ select   SPACE toggle   1 on   0 off   +/- PWM ±15   p PWM value   "
    "r read   : command   q quit "
)


class GpioTui:
    """curses TUI: live pin table + command prompt."""

    def __init__(self, gpio: GpioApplicationService, interpreter: CommandInterpreter,
                 backend_name: str):
        self._gpio = gpio
        self._interp = interpreter
        self._backend_name = backend_name
        self._selected = 0
        self._offset = 0
        self._message = "Welcome — press ':' for the command prompt, 'q' to quit."

    def run(self):
        import curses
        curses.wrapper(self._main)

    # -- internals -------------------------------------------------------

    def _main(self, stdscr):
        import curses
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)    # ON
        curses.init_pair(2, curses.COLOR_RED, -1)      # OFF
        curses.init_pair(3, curses.COLOR_YELLOW, -1)   # PWM
        curses.init_pair(4, curses.COLOR_CYAN, -1)     # capabilities
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # reserved
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selection bar

        while True:
            self._draw(stdscr, curses)
            key = stdscr.getch()
            if not self._handle_key(stdscr, curses, key):
                break

    def _handle_key(self, stdscr, curses, key) -> bool:
        if "\n" in self._message:   # help overlay open — any key closes it
            self._message = "Help closed — ':help <topic>' any time."
            return True
        pins = self._gpio.status()
        pin = pins[self._selected]
        try:
            if key in (ord("q"), ord("Q")):
                return False
            elif key in (curses.KEY_UP, ord("k")):
                self._selected = max(0, self._selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self._selected = min(len(pins) - 1, self._selected + 1)
            elif key in (ord(" "), curses.KEY_ENTER, 10, 13):
                self._message = self._gpio.toggle(pin.bcm)
            elif key == ord("1"):
                self._message = self._gpio.turn_on(pin.bcm)
            elif key == ord("0"):
                self._message = self._gpio.turn_off(pin.bcm)
            elif key == ord("r"):
                self._message = self._gpio.read(pin.bcm)
            elif key in (ord("+"), ord("=")):
                self._message = self._gpio.set_pwm(pin.bcm, min(255, pin.pwm_value + 15))
            elif key in (ord("-"), ord("_")):
                self._message = self._gpio.set_pwm(pin.bcm, max(0, pin.pwm_value - 15))
            elif key == ord("p"):
                raw = self._prompt(stdscr, curses, f"PWM value for GPIO{pin.bcm} (0-255): ")
                if raw:
                    self._message = self._gpio.set_pwm(pin.bcm, int(raw))
            elif key == ord(":"):
                line = self._prompt(stdscr, curses, ": ")
                if line:
                    self._message, should_quit = self._interp.execute(line)
                    if should_quit:
                        return False
        except GpioDomainException as exc:
            self._message = f"! {exc}"
        except ValueError:
            self._message = "! Not a number."
        return True

    def _prompt(self, stdscr, curses, label: str) -> str:
        h, w = stdscr.getmaxyx()
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        stdscr.addstr(h - 1, 0, label, curses.A_BOLD)
        curses.echo()
        curses.curs_set(1)
        try:
            raw = stdscr.getstr(h - 1, len(label), max(1, w - len(label) - 2))
            return raw.decode(errors="replace").strip()
        finally:
            curses.noecho()
            curses.curs_set(0)

    def _draw(self, stdscr, curses):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        pins = self._gpio.status()

        title = f" gpioctl — Raspberry Pi Zero 2 W  [{self._backend_name}] "
        stdscr.addnstr(0, 0, title.center(w, "─"), w - 1, curses.A_BOLD)

        header = f" {'BCM':<7}{'HDR':<5}{'MODE':<7}{'STATE':<8}{'PWM':<9}{'FUNCTIONS'}"
        stdscr.addnstr(1, 0, header, w - 1, curses.A_UNDERLINE)

        visible_rows = h - 5   # title, header, help, message, prompt
        if self._selected < self._offset:
            self._offset = self._selected
        elif self._selected >= self._offset + visible_rows:
            self._offset = self._selected - visible_rows + 1

        for row, dto in enumerate(pins[self._offset:self._offset + visible_rows]):
            y = 2 + row
            idx = self._offset + row
            selected = idx == self._selected

            if dto.mode == "pwm":
                state, state_attr = f"PWM", curses.color_pair(3)
                pwm_col = f"{dto.pwm_value}/255"
            elif dto.mode == "out":
                state = "ON " if dto.level else "OFF"
                state_attr = curses.color_pair(1) if dto.level else curses.color_pair(2)
                pwm_col = "-"
            elif dto.mode == "in":
                state, state_attr = f"IN={dto.level}", curses.color_pair(4)
                pwm_col = "-"
            else:
                state, state_attr = "·", curses.A_DIM
                pwm_col = "0" if any(dto.bcm == p for p in PWM_CAPABLE_PINS) else "-"

            line = f" GPIO{dto.bcm:<3}{dto.header:<5}{dto.mode:<7}"
            stdscr.addnstr(y, 0, line, w - 1,
                           curses.color_pair(6) if selected else curses.A_NORMAL)
            x = len(line)
            stdscr.addnstr(y, x, f"{state:<8}", max(1, w - 1 - x),
                           (curses.color_pair(6) if selected else state_attr) | curses.A_BOLD)
            x += 8
            stdscr.addnstr(y, x, f"{pwm_col:<9}", max(1, w - 1 - x),
                           curses.color_pair(6) if selected else curses.color_pair(3))
            x += 9
            caps = dto.capabilities + (f"  [RESERVED: {dto.reserved}]" if dto.reserved else "")
            caps_attr = curses.color_pair(5) if dto.reserved else curses.color_pair(4)
            stdscr.addnstr(y, x, caps, max(1, w - 1 - x),
                           curses.color_pair(6) if selected else caps_attr)

        stdscr.addnstr(h - 3, 0, TUI_KEY_HELP[:w - 1], w - 1, curses.A_REVERSE)
        first_line = self._message.splitlines()[0] if self._message else ""
        stdscr.addnstr(h - 2, 0, f" {first_line}", w - 1, curses.A_BOLD)
        if "\n" in self._message:
            self._draw_help_overlay(stdscr, curses)
        stdscr.refresh()

    def _draw_help_overlay(self, stdscr, curses):
        lines = self._message.splitlines()
        h, w = stdscr.getmaxyx()
        box_w = min(w - 2, max(len(l) for l in lines) + 4)
        box_h = min(h - 2, len(lines) + 2)
        y0 = max(0, (h - box_h) // 2)
        x0 = max(0, (w - box_w) // 2)
        for i in range(box_h):
            stdscr.addnstr(y0 + i, x0, " " * box_w, box_w, curses.A_REVERSE)
        for i, line in enumerate(lines[:box_h - 2]):
            stdscr.addnstr(y0 + 1 + i, x0 + 2, line, max(1, box_w - 4),
                           curses.A_REVERSE)
        footer = " any key to close "
        stdscr.addnstr(y0 + box_h - 1, x0 + max(0, (box_w - len(footer)) // 2),
                       footer, max(1, box_w - 1), curses.A_REVERSE | curses.A_BOLD)


# ============================================================================
# [presentation] CLI
# ============================================================================

def _print_status(dtos: List[PinStatusDto]) -> None:
    print(f"{'BCM':<8}{'HDR':<5}{'MODE':<7}{'LVL':<5}{'PWM':<9}FUNCTIONS")
    print("-" * 70)
    for d in dtos:
        pwm = f"{d.pwm_value}/255" if d.mode == "pwm" else ("0" if d.bcm in PWM_CAPABLE_PINS else "-")
        caps = d.capabilities + (f"  [RESERVED: {d.reserved}]" if d.reserved else "")
        print(f"GPIO{d.bcm:<4}{d.header:<5}{d.mode:<7}{d.level:<5}{pwm:<9}{caps}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpioctl",
        description="Raspberry Pi Zero 2 W GPIO controller (CLI + TUI).",
        epilog=f"PWM-capable pins: {', '.join('BCM%d' % b for b in PWM_CAPABLE_PINS)}. "
               f"UART: BCM14/15. I2C: BCM2/3. SPI0: BCM7-11.",
    )
    p.add_argument("--mock", action="store_true", help="force the mock backend")
    p.add_argument("--force", action="store_true", help="allow touching reserved pins (BCM0/1)")
    sub = p.add_subparsers(dest="command", required=True)

    for name, help_text in [("on", "drive a pin high"), ("off", "drive a pin low"),
                            ("toggle", "toggle a pin")]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("pin", type=int, help="BCM pin number")

    sp = sub.add_parser("read", help="read a pin as input")
    sp.add_argument("pin", type=int)
    sp.add_argument("--pull", choices=["up", "down", "none"], default="none")

    sp = sub.add_parser("pwm", help="set PWM on a PWM-capable pin (holds until Ctrl+C)")
    sp.add_argument("pin", type=int)
    sp.add_argument("value", type=int, nargs="?", default=0,
                    help="0 = PWM off (default), 1-255 = duty cycle")

    sub.add_parser("status", help="show all pins and capabilities")
    sub.add_parser("all-off", help="switch every active output/PWM pin off")
    sub.add_parser("tui", help="launch the interactive TUI")

    sp = sub.add_parser("help", help="topic help, e.g. 'help pwm' (see 'help help')")
    sp.add_argument("topic", nargs="?", default=None,
                    help=f"one of: {', '.join(sorted(HELP_TOPICS))}")

    sp = sub.add_parser("serial", help="UART on GPIO14 (TXD) / GPIO15 (RXD)")
    ssub = sp.add_subparsers(dest="serial_command", required=True)
    s_send = ssub.add_parser("send")
    s_send.add_argument("text")
    s_send.add_argument("--port", default=DEFAULT_SERIAL_PORT)
    s_send.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    s_read = ssub.add_parser("read")
    s_read.add_argument("--seconds", type=float, default=3.0)
    s_read.add_argument("--port", default=DEFAULT_SERIAL_PORT)
    s_read.add_argument("--baud", type=int, default=DEFAULT_BAUD)

    sp = sub.add_parser("i2c", help="I2C1 on GPIO2 (SDA) / GPIO3 (SCL)")
    isub = sp.add_subparsers(dest="i2c_command", required=True)
    isub.add_parser("scan")
    i_read = isub.add_parser("read")
    i_read.add_argument("addr", type=lambda s: int(s, 0))
    i_read.add_argument("reg", type=lambda s: int(s, 0))
    i_write = isub.add_parser("write")
    i_write.add_argument("addr", type=lambda s: int(s, 0))
    i_write.add_argument("reg", type=lambda s: int(s, 0))
    i_write.add_argument("value", type=lambda s: int(s, 0))

    sp = sub.add_parser("spi", help="SPI0 on GPIO9/10/11, CE0=GPIO8, CE1=GPIO7")
    spsub = sp.add_subparsers(dest="spi_command", required=True)
    s_xfer = spsub.add_parser("xfer")
    s_xfer.add_argument("data", nargs="+", type=lambda s: int(s, 0))
    s_xfer.add_argument("--bus", type=int, default=0)
    s_xfer.add_argument("--device", type=int, default=0)
    s_xfer.add_argument("--speed", type=int, default=500000)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "help":          # no backend needed for help
        print(usage_text(args.topic))
        return 0

    backend = create_backend(force_mock=args.mock)
    board = GpioBoard(backend)
    gpio = GpioApplicationService(board)
    bus = BusApplicationService()

    try:
        if args.command == "on":
            print(gpio.turn_on(args.pin, args.force))
        elif args.command == "off":
            print(gpio.turn_off(args.pin, args.force))
        elif args.command == "toggle":
            print(gpio.toggle(args.pin, args.force))
        elif args.command == "read":
            print(gpio.read(args.pin, args.pull, args.force))
        elif args.command == "pwm":
            print(gpio.set_pwm(args.pin, args.value, args.force))
            if args.value > 0:
                print("Software PWM runs inside this process — holding. Ctrl+C to stop.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print(gpio.set_pwm(args.pin, 0, args.force))
        elif args.command == "status":
            _print_status(gpio.status())
        elif args.command == "all-off":
            print(gpio.all_off(args.force))
        elif args.command == "serial":
            if args.serial_command == "send":
                print(bus.serial_send(args.text, args.port, args.baud))
            else:
                print(bus.serial_read(args.seconds, args.port, args.baud))
        elif args.command == "i2c":
            if args.i2c_command == "scan":
                print(bus.i2c_scan())
            elif args.i2c_command == "read":
                print(bus.i2c_read(args.addr, args.reg))
            else:
                print(bus.i2c_write(args.addr, args.reg, args.value))
        elif args.command == "spi":
            print(bus.spi_transfer(args.data, args.bus, args.device, args.speed))
        elif args.command == "tui":
            interpreter = CommandInterpreter(gpio, bus)
            GpioTui(gpio, interpreter, backend.name).run()
            gpio.release()
    except GpioDomainException as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
