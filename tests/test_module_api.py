"""Public module API: import gpioctl and pull the strings directly."""
import pytest

import gpioctl


@pytest.fixture(autouse=True)
def use_mock_backend():
    gpioctl.configure(mock=True)
    yield


class TestPinFunctions:
    def test_on_off_toggle_return_levels(self):
        assert gpioctl.on(17) == 1
        assert gpioctl.off(17) == 0
        assert gpioctl.toggle(17) == 1

    def test_read(self):
        assert gpioctl.read(4, pull="up") == 1

    def test_pwm_value_defaults_to_zero(self):
        assert gpioctl.pwm(18) == 0

    def test_pwm_with_value(self):
        assert gpioctl.pwm(18, 128) == 128

    def test_pwm_rejects_non_pwm_pin(self):
        with pytest.raises(gpioctl.PwmNotSupportedException):
            gpioctl.pwm(22, 10)

    def test_status(self):
        gpioctl.on(17)
        assert gpioctl.status()[17].level == 1

    def test_all_off(self):
        gpioctl.on(17)
        assert gpioctl.all_off() == [17]

    def test_release_without_board_is_noop(self):
        gpioctl._module_board = None
        gpioctl.release()

    def test_release_stops_pwm(self):
        gpioctl.pwm(18, 10)
        gpioctl.release()
        assert gpioctl.status()[18].pwm_value == 0

    def test_configure_resets_an_existing_board(self):
        gpioctl.on(17)
        gpioctl.configure(mock=True)
        assert gpioctl.status()[17].level == 0


class TestBusFunctions:
    def test_serial_send(self, fake_serial):
        assert gpioctl.serial_send("hi") == 3

    def test_serial_send_without_newline(self, fake_serial):
        assert gpioctl.serial_send("hi", newline=False) == 2

    def test_serial_read(self, fake_serial):
        port, _ = fake_serial
        chunks = [b"pong"]
        port.read.side_effect = lambda n: chunks.pop(0) if chunks else b""
        assert gpioctl.serial_read(seconds=0.05) == b"pong"

    def test_i2c_functions(self, fake_smbus):
        assert gpioctl.i2c_scan() == [0x48]
        assert gpioctl.i2c_read(0x48, 0x00) == 0x2A
        gpioctl.i2c_write(0x48, 0x01, 0xFF)

    def test_spi_xfer(self, fake_spidev):
        assert gpioctl.spi_xfer([0x9F, 0x00]) == [0xEF, 0xEF]


class TestModuleSurface:
    def test_all_exports_exist(self):
        for name in gpioctl.__all__:
            assert hasattr(gpioctl, name), name
