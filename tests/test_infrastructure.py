"""Infrastructure layer: backends and UART/I2C/SPI adapters."""
import sys

import pytest

from gpioctl import (
    BusUnavailableException,
    I2cBusAdapter,
    IGpioBackend,
    MockGpioBackend,
    RpiGpioBackend,
    SerialPortAdapter,
    SpiBusAdapter,
    create_backend,
)


class TestIGpioBackendContract:
    def test_every_method_is_abstract(self):
        backend = IGpioBackend()
        with pytest.raises(NotImplementedError):
            backend.setup_output(1)
        with pytest.raises(NotImplementedError):
            backend.setup_input(1, "up")
        with pytest.raises(NotImplementedError):
            backend.write(1, 1)
        with pytest.raises(NotImplementedError):
            backend.read(1)
        with pytest.raises(NotImplementedError):
            backend.pwm_start(1, 1000, 50)
        with pytest.raises(NotImplementedError):
            backend.pwm_set_duty(1, 50)
        with pytest.raises(NotImplementedError):
            backend.pwm_stop(1)


class TestMockGpioBackend:
    def test_write_and_read(self):
        backend = MockGpioBackend()
        backend.setup_output(17)
        assert backend.read(17) == 0
        backend.write(17, 1)
        assert backend.read(17) == 1

    def test_input_pull_levels(self):
        backend = MockGpioBackend()
        backend.setup_input(4, "up")
        assert backend.read(4) == 1
        backend.setup_input(4, "down")
        assert backend.read(4) == 0

    def test_pwm_lifecycle(self):
        backend = MockGpioBackend()
        backend.pwm_start(18, 1000, 50)
        assert backend.read(18) == 1
        backend.pwm_set_duty(18, 75)
        backend.pwm_stop(18)
        assert backend.read(18) == 0


class TestRpiGpioBackend:
    def test_full_lifecycle(self, fake_rpi):
        backend = RpiGpioBackend()
        fake_rpi.setmode.assert_called_once_with("BCM")
        backend.setup_output(17)
        backend.write(17, 1)
        backend.write(17, 0)
        backend.setup_input(4, "up")
        backend.setup_input(4, "down")
        backend.setup_input(4, "none")
        assert backend.read(4) == 1
        backend.pwm_start(18, 1000, 50)
        backend.pwm_set_duty(18, 99)
        backend.pwm_stop(18)
        backend.pwm_stop(18)  # no PWM registered any more -> silent no-op

    def test_pull_constant_mapping(self, fake_rpi):
        backend = RpiGpioBackend()
        backend.setup_input(4, "up")
        assert fake_rpi.setup.call_args.kwargs["pull_up_down"] == "UP"


class TestCreateBackend:
    def test_force_mock(self):
        assert isinstance(create_backend(force_mock=True), MockGpioBackend)

    def test_real_backend_when_rpi_available(self, fake_rpi):
        assert isinstance(create_backend(), RpiGpioBackend)

    def test_falls_back_when_rpi_missing(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "RPi", None)
        monkeypatch.setitem(sys.modules, "RPi.GPIO", None)
        assert isinstance(create_backend(), MockGpioBackend)
        assert "MOCK" in capsys.readouterr().err

    def test_falls_back_on_runtime_error(self, fake_rpi):
        fake_rpi.setmode.side_effect = RuntimeError("not a pi")
        assert isinstance(create_backend(), MockGpioBackend)


class TestSerialPortAdapter:
    def test_requires_pyserial(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "serial", None)
        with pytest.raises(BusUnavailableException, match="pyserial"):
            SerialPortAdapter()

    def test_wraps_open_failure(self, fake_serial):
        _, module = fake_serial
        module.Serial.side_effect = Exception("port busy")
        with pytest.raises(BusUnavailableException, match="raspi-config"):
            SerialPortAdapter()

    def test_send_appends_newline(self, fake_serial):
        port, _ = fake_serial
        adapter = SerialPortAdapter()
        assert adapter.send("hi") == 3
        port.write.assert_called_with(b"hi\n")
        adapter.close()

    def test_send_raw(self, fake_serial):
        adapter = SerialPortAdapter()
        assert adapter.send("hi", newline=False) == 2

    def test_read_for_collects_chunks(self, fake_serial):
        port, _ = fake_serial
        chunks = [b"he", b"llo"]
        port.read.side_effect = lambda n: chunks.pop(0) if chunks else b""
        adapter = SerialPortAdapter()
        assert adapter.read_for(0.05) == b"hello"


class TestI2cBusAdapter:
    def test_requires_smbus2(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "smbus2", None)
        with pytest.raises(BusUnavailableException, match="smbus2"):
            I2cBusAdapter()

    def test_wraps_open_failure(self, fake_smbus):
        _, module = fake_smbus
        module.SMBus.side_effect = Exception("no bus")
        with pytest.raises(BusUnavailableException, match="Cannot open I2C"):
            I2cBusAdapter()

    def test_scan_finds_acking_devices(self, fake_smbus):
        adapter = I2cBusAdapter()
        assert adapter.scan() == [0x48]
        adapter.close()

    def test_register_read_write(self, fake_smbus):
        bus, _ = fake_smbus
        adapter = I2cBusAdapter()
        assert adapter.read_register(0x48, 0x00) == 0x2A
        adapter.write_register(0x48, 0x01, 0xFF)
        bus.write_byte_data.assert_called_with(0x48, 0x01, 0xFF)


class TestSpiBusAdapter:
    def test_requires_spidev(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "spidev", None)
        with pytest.raises(BusUnavailableException, match="spidev"):
            SpiBusAdapter()

    def test_wraps_open_failure(self, fake_spidev):
        spi, _ = fake_spidev
        spi.open.side_effect = Exception("nope")
        with pytest.raises(BusUnavailableException, match="Cannot open SPI"):
            SpiBusAdapter()

    def test_transfer(self, fake_spidev):
        adapter = SpiBusAdapter()
        assert adapter.transfer([0x9F]) == [0xEF]
        adapter.close()
