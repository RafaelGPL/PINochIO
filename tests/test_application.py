"""Application layer: services, command interpreter, topic help."""
import pytest

import gpioctl


class TestGpioApplicationService:
    def test_turn_on_and_off_messages(self, gpio_service):
        assert "ON" in gpio_service.turn_on(17)
        assert "OFF" in gpio_service.turn_off(17)

    def test_toggle_messages(self, gpio_service):
        assert gpio_service.toggle(17).endswith("ON")
        assert gpio_service.toggle(17).endswith("OFF")

    def test_read_message(self, gpio_service):
        message = gpio_service.read(4, pull="up")
        assert "HIGH" in message and "pull=up" in message

    def test_set_pwm_messages(self, gpio_service):
        assert "50.2%" in gpio_service.set_pwm(18, 128)
        assert "disabled" in gpio_service.set_pwm(18, 0)

    def test_all_off_messages(self, gpio_service):
        assert "nothing active" in gpio_service.all_off()
        gpio_service.turn_on(17)
        assert "GPIO17" in gpio_service.all_off()

    def test_status_snapshot(self, gpio_service):
        gpio_service.set_pwm(18, 10)
        dtos = gpio_service.status()
        assert len(dtos) == 28
        assert (dtos[18].mode, dtos[18].pwm_value) == ("pwm", 10)
        assert dtos[0].reserved is not None

    def test_release(self, gpio_service):
        gpio_service.set_pwm(18, 10)
        gpio_service.release()
        assert gpio_service.status()[18].pwm_value == 0


class TestSelfTest:
    def test_blinks_every_non_reserved_pin_one_second_apart(self, board):
        sleeps, events = [], []
        service = gpioctl.GpioApplicationService(board, sleep=sleeps.append)
        message = service.run_self_test(observer=events.append)
        assert "Self-test complete: 26 pins blinked" in message
        assert events[0] == "Self-test: GPIO2 -> 1"
        assert events[1] == "Self-test: GPIO2 -> 0"
        assert sleeps.count(gpioctl.SELF_TEST_BLINK_SECONDS) == 26

    def test_heartbeat_pulses_pwm_pins_for_five_seconds(self, board):
        sleeps = []
        service = gpioctl.GpioApplicationService(board, sleep=sleeps.append)
        events = []
        service.run_self_test(observer=events.append)
        heartbeats = [e for e in events if "heartbeat" in e]
        assert len(heartbeats) == gpioctl.SELF_TEST_HEARTBEAT_SECONDS
        assert "GPIO12, GPIO13, GPIO18, GPIO19" in heartbeats[0]
        # 26 blink sleeps + 5 cycles x 5 pattern steps, one second per cycle
        assert len(sleeps) == 26 + 5 * len(gpioctl.SELF_TEST_HEARTBEAT_PATTERN)
        assert abs(sum(sleeps) - 31.0) < 1e-9

    def test_everything_ends_switched_off(self, board):
        service = gpioctl.GpioApplicationService(board, sleep=lambda s: None)
        service.run_self_test()
        assert all(p.level == 0 for p in board.all_pins())
        assert board.pin(18).pwm_value == 0

    def test_reserved_pins_are_skipped(self, board):
        service = gpioctl.GpioApplicationService(board, sleep=lambda s: None)
        service.run_self_test()
        assert board.pin(0).mode == gpioctl.PinMode.UNSET

    def test_default_observer_is_silent(self, board):
        service = gpioctl.GpioApplicationService(board, sleep=lambda s: None)
        assert "complete" in service.run_self_test()


class TestBusApplicationService:
    def test_serial_send(self, bus_service, fake_serial):
        assert "Sent 3 bytes" in bus_service.serial_send("hi")

    def test_serial_read_without_data(self, bus_service, fake_serial):
        assert "No data" in bus_service.serial_read(seconds=0.01)

    def test_serial_read_with_data(self, bus_service, fake_serial):
        port, _ = fake_serial
        chunks = [b"pong"]
        port.read.side_effect = lambda n: chunks.pop(0) if chunks else b""
        assert "4 bytes" in bus_service.serial_read(seconds=0.05)

    def test_i2c_scan_found(self, bus_service, fake_smbus):
        assert "0x48" in bus_service.i2c_scan()

    def test_i2c_scan_empty(self, bus_service, fake_smbus):
        bus, _ = fake_smbus
        bus.write_quick.side_effect = OSError("silence")
        assert "no devices" in bus_service.i2c_scan()

    def test_i2c_read(self, bus_service, fake_smbus):
        assert "0x2A" in bus_service.i2c_read(0x48, 0x00)

    def test_i2c_write(self, bus_service, fake_smbus):
        assert "wrote" in bus_service.i2c_write(0x48, 0x01, 0xFF)

    def test_spi_transfer(self, bus_service, fake_spidev):
        assert "0xEF" in bus_service.spi_transfer([0x9F])


class TestCommandInterpreter:
    def test_empty_line(self, interpreter):
        assert interpreter.execute("") == ("", False)

    @pytest.mark.parametrize("alias", ["quit", "q", "exit"])
    def test_quit_aliases(self, interpreter, alias):
        assert interpreter.execute(alias) == ("bye", True)

    def test_help_summary(self, interpreter):
        message, _ = interpreter.execute("help")
        assert "pwm <pin>" in message

    def test_help_topic(self, interpreter):
        message, _ = interpreter.execute("help pwm")
        assert "duty" in message

    def test_on_off_toggle(self, interpreter):
        assert "ON" in interpreter.execute("on 17")[0]
        assert "OFF" in interpreter.execute("off 17")[0]
        assert "GPIO17" in interpreter.execute("toggle 17")[0]

    def test_read_with_and_without_pull(self, interpreter):
        assert "pull=up" in interpreter.execute("read 4 up")[0]
        assert "pull=none" in interpreter.execute("read 4")[0]

    def test_pwm_value_defaults_to_zero(self, interpreter):
        assert "disabled" in interpreter.execute("pwm 18")[0]

    def test_pwm_with_value(self, interpreter):
        assert "128/255" in interpreter.execute("pwm 18 128")[0]

    def test_alloff(self, interpreter):
        assert "Switched off" in interpreter.execute("alloff")[0]

    def test_self_test_command(self, interpreter):
        assert "Self-test complete" in interpreter.execute("test")[0]

    def test_self_test_alias_reports_to_observer(self, interpreter):
        events = []
        interpreter.observer = events.append
        assert "Self-test complete" in interpreter.execute("selftest")[0]
        assert any("heartbeat" in e for e in events)

    def test_serial_commands(self, interpreter, fake_serial):
        assert "Sent" in interpreter.execute("serial send hello world")[0]
        assert "No data" in interpreter.execute("serial read 0.01")[0]

    def test_i2c_commands(self, interpreter, fake_smbus):
        assert "0x48" in interpreter.execute("i2c scan")[0]
        assert "0x2A" in interpreter.execute("i2c read 0x48 0x00")[0]
        assert "wrote" in interpreter.execute("i2c write 0x48 0x01 0xFF")[0]

    def test_spi_command(self, interpreter, fake_spidev):
        assert "0xEF" in interpreter.execute("spi xfer 0x9F 0x00")[0]

    def test_unknown_command(self, interpreter):
        assert "Unknown command" in interpreter.execute("dance 17")[0]

    def test_domain_error_is_reported(self, interpreter):
        assert interpreter.execute("pwm 22 100")[0].startswith("!")

    def test_bad_arguments(self, interpreter):
        assert "Bad arguments" in interpreter.execute("on")[0]
        assert "Bad arguments" in interpreter.execute("on x")[0]


class TestUsageHelp:
    def test_default_is_overview(self):
        assert "Three ways" in gpioctl.usage_text()

    def test_topic(self):
        assert "duty" in gpioctl.usage_text("pwm")

    def test_alias_resolution(self):
        assert gpioctl.usage_text("uart") == gpioctl.usage_text("serial")
        assert gpioctl.usage_text("scripting") == gpioctl.usage_text("import")
        assert gpioctl.usage_text("selftest") == gpioctl.usage_text("test")

    def test_self_test_topic(self):
        assert "heartbeat" in gpioctl.usage_text("test")

    def test_unknown_topic(self):
        assert "Unknown help topic" in gpioctl.usage_text("bogus")

    def test_pins_topic_is_generated_from_pin_table(self):
        text = gpioctl.usage_text("pins")
        assert "GPIO18" in text and "RESERVED" in text

    def test_usage_prints(self, capsys):
        gpioctl.usage("pwm")
        assert "duty" in capsys.readouterr().out
