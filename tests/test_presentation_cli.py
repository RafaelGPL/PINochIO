"""Presentation layer: argparse CLI (main entry point)."""
import sys

import pytest

import gpioctl


def run(args):
    return gpioctl.main(args)


class TestPinCommands:
    def test_on(self, capsys):
        assert run(["--mock", "on", "17"]) == 0
        assert "ON" in capsys.readouterr().out

    def test_off(self, capsys):
        assert run(["--mock", "off", "17"]) == 0
        assert "OFF" in capsys.readouterr().out

    def test_toggle(self, capsys):
        assert run(["--mock", "toggle", "17"]) == 0
        assert "GPIO17" in capsys.readouterr().out

    def test_read(self, capsys):
        assert run(["--mock", "read", "4", "--pull", "up"]) == 0
        assert "HIGH" in capsys.readouterr().out

    def test_pwm_zero_does_not_hold(self, capsys):
        assert run(["--mock", "pwm", "18"]) == 0
        out = capsys.readouterr().out
        assert "disabled" in out and "holding" not in out

    def test_pwm_holds_until_interrupt(self, monkeypatch, capsys):
        def interrupt(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(gpioctl.time, "sleep", interrupt)
        assert run(["--mock", "pwm", "18", "128"]) == 0
        out = capsys.readouterr().out
        assert "holding" in out and "disabled" in out

    def test_pwm_error_sets_exit_code(self, capsys):
        assert run(["--mock", "pwm", "22", "100"]) == 1
        assert "error:" in capsys.readouterr().err

    def test_reserved_pin_needs_force(self, capsys):
        assert run(["--mock", "on", "0"]) == 1
        assert run(["--mock", "--force", "on", "0"]) == 0

    def test_status(self, capsys):
        assert run(["--mock", "status"]) == 0
        out = capsys.readouterr().out
        assert "GPIO18" in out and "RESERVED" in out

    def test_all_off(self, capsys):
        assert run(["--mock", "all-off"]) == 0
        assert "Switched off" in capsys.readouterr().out


class TestHelpCommand:
    def test_overview(self, capsys):
        assert run(["help"]) == 0
        assert "Three ways" in capsys.readouterr().out

    def test_topic(self, capsys):
        assert run(["help", "pwm"]) == 0
        assert "duty" in capsys.readouterr().out

    def test_unknown_topic(self, capsys):
        assert run(["help", "bogus"]) == 0
        assert "Unknown help topic" in capsys.readouterr().out


class TestBusCommands:
    def test_serial_send(self, capsys, fake_serial):
        assert run(["--mock", "serial", "send", "hi"]) == 0
        assert "Sent" in capsys.readouterr().out

    def test_serial_read(self, capsys, fake_serial):
        assert run(["--mock", "serial", "read", "--seconds", "0.01"]) == 0
        assert "No data" in capsys.readouterr().out

    def test_i2c_scan(self, capsys, fake_smbus):
        assert run(["--mock", "i2c", "scan"]) == 0
        assert "0x48" in capsys.readouterr().out

    def test_i2c_read(self, capsys, fake_smbus):
        assert run(["--mock", "i2c", "read", "0x48", "0x00"]) == 0
        assert "0x2A" in capsys.readouterr().out

    def test_i2c_write(self, capsys, fake_smbus):
        assert run(["--mock", "i2c", "write", "0x48", "0x01", "0xFF"]) == 0
        assert "wrote" in capsys.readouterr().out

    def test_spi_xfer(self, capsys, fake_spidev):
        assert run(["--mock", "spi", "xfer", "0x9F"]) == 0
        assert "0xEF" in capsys.readouterr().out

    def test_bus_error_sets_exit_code(self, capsys, monkeypatch):
        monkeypatch.setitem(sys.modules, "serial", None)
        assert run(["--mock", "serial", "send", "hi"]) == 1
        assert "error:" in capsys.readouterr().err


class TestTuiCommand:
    def test_tui_launch_and_quit(self, fake_curses_factory, stdscr_cls):
        screen = stdscr_cls(keys=[ord("q")])
        fake_curses_factory(screen)
        assert run(["--mock", "tui"]) == 0
        assert screen.drawn  # the pin table was rendered
