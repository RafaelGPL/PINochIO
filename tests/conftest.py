"""Shared fixtures for the PINochIO test suite.

None of the hardware libraries (RPi.GPIO, pyserial, smbus2, spidev) nor
curses are assumed to exist on the test machine — each one gets an
in-memory fake injected into sys.modules, which the code under test picks
up through its deferred imports.
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gpioctl  # noqa: E402


@pytest.fixture(autouse=True)
def reset_module_state():
    """Keep the lazy module-level board from leaking between tests."""
    yield
    gpioctl._module_board = None
    gpioctl._module_mock = False


# -- domain / application fixtures -------------------------------------------

@pytest.fixture
def board():
    return gpioctl.GpioBoard(gpioctl.MockGpioBackend())


@pytest.fixture
def gpio_service(board):
    # no-op sleep so the self-test sequence runs instantly under test
    return gpioctl.GpioApplicationService(board, sleep=lambda seconds: None)


@pytest.fixture
def bus_service():
    return gpioctl.BusApplicationService()


@pytest.fixture
def interpreter(gpio_service, bus_service):
    return gpioctl.CommandInterpreter(gpio_service, bus_service)


# -- fake hardware modules ----------------------------------------------------

@pytest.fixture
def fake_rpi(monkeypatch):
    """Fake RPi.GPIO module; returns the GPIO mock for assertions."""
    gpio = MagicMock()
    gpio.BCM, gpio.OUT, gpio.IN = "BCM", "OUT", "IN"
    gpio.HIGH, gpio.LOW = 1, 0
    gpio.PUD_UP, gpio.PUD_DOWN, gpio.PUD_OFF = "UP", "DOWN", "OFF"
    gpio.input.return_value = 1
    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    return gpio


@pytest.fixture
def fake_serial(monkeypatch):
    """Fake pyserial; returns (port, module)."""
    port = MagicMock()
    port.write.side_effect = lambda data: len(data)
    port.read.return_value = b""
    module = types.ModuleType("serial")
    module.Serial = MagicMock(return_value=port)
    monkeypatch.setitem(sys.modules, "serial", module)
    return port, module


@pytest.fixture
def fake_smbus(monkeypatch):
    """Fake smbus2 with one device at 0x48; returns (bus, module)."""
    bus = MagicMock()

    def write_quick(addr):
        if addr != 0x48:
            raise OSError("no ack")

    bus.write_quick.side_effect = write_quick
    bus.read_byte_data.return_value = 0x2A
    module = types.ModuleType("smbus2")
    module.SMBus = MagicMock(return_value=bus)
    monkeypatch.setitem(sys.modules, "smbus2", module)
    return bus, module


@pytest.fixture
def fake_spidev(monkeypatch):
    """Fake spidev echoing 0xEF per byte; returns (spi, module)."""
    spi = MagicMock()
    spi.xfer2.side_effect = lambda data: [0xEF] * len(data)
    module = types.ModuleType("spidev")
    module.SpiDev = MagicMock(return_value=spi)
    monkeypatch.setitem(sys.modules, "spidev", module)
    return spi, module


# -- fake curses ---------------------------------------------------------------

class FakeStdscr:
    """Scriptable stand-in for a curses window: feed keys and prompt entries."""

    def __init__(self, keys=(ord("q"),), entries=(), size=(24, 80)):
        self.keys = list(keys)
        self.entries = list(entries)
        self.size = size
        self.drawn = []

    def getmaxyx(self):
        return self.size

    def getch(self):
        return self.keys.pop(0)

    def getstr(self, y, x, n):
        return self.entries.pop(0)

    def addnstr(self, y, x, text, n, attr=0):
        self.drawn.append(text)

    def addstr(self, y, x, text, attr=0):
        self.drawn.append(text)

    def erase(self):
        pass

    def move(self, y, x):
        pass

    def clrtoeol(self):
        pass

    def refresh(self):
        pass


@pytest.fixture
def stdscr_cls():
    return FakeStdscr


@pytest.fixture
def fake_curses_factory(monkeypatch):
    """Install a fake curses module whose wrapper() drives the given stdscr."""

    def build(stdscr):
        c = types.ModuleType("curses")
        c.KEY_UP, c.KEY_DOWN, c.KEY_ENTER = 259, 258, 343
        c.A_BOLD, c.A_UNDERLINE, c.A_REVERSE, c.A_DIM, c.A_NORMAL = 1, 2, 4, 8, 0
        c.COLOR_GREEN = c.COLOR_RED = c.COLOR_YELLOW = 0
        c.COLOR_CYAN = c.COLOR_MAGENTA = c.COLOR_BLACK = 0
        c.color_pair = lambda n: 0
        c.init_pair = lambda *a: None
        c.curs_set = lambda n: None
        c.use_default_colors = lambda: None
        c.echo = lambda: None
        c.noecho = lambda: None
        c.wrapper = lambda fn: fn(stdscr)
        monkeypatch.setitem(sys.modules, "curses", c)
        return c

    return build
