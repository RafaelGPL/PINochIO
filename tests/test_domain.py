"""Domain layer: PinDefinition, Pin, GpioBoard aggregate, exceptions."""
import pytest

from gpioctl import (
    PIN_TABLE,
    PWM_CAPABLE_PINS,
    InvalidPwmValueException,
    PinMode,
    PinNotFoundException,
    PinReservedException,
    PwmNotSupportedException,
)


class TestPinDefinition:
    def test_label(self):
        assert PIN_TABLE[17].label == "GPIO17"

    def test_capability_summary_plain_digital(self):
        assert PIN_TABLE[22].capability_summary == "digital"

    def test_capability_summary_alt_function(self):
        assert PIN_TABLE[2].capability_summary == "I2C1_SDA"

    def test_capability_summary_lists_pwm_first(self):
        assert PIN_TABLE[18].capability_summary.startswith("PWM0")

    def test_pwm_capable_pins(self):
        assert PWM_CAPABLE_PINS == [12, 13, 18, 19]

    def test_reserved_pins(self):
        assert PIN_TABLE[0].reserved and PIN_TABLE[1].reserved


class TestGpioBoard:
    def test_unknown_pin_raises(self, board):
        with pytest.raises(PinNotFoundException):
            board.pin(99)

    def test_all_pins_covers_header(self, board):
        assert len(board.all_pins()) == 28

    def test_switch_on(self, board):
        pin = board.switch_on(17)
        assert (pin.mode, pin.level) == (PinMode.OUTPUT, 1)

    def test_switch_off(self, board):
        pin = board.switch_off(17)
        assert (pin.mode, pin.level) == (PinMode.OUTPUT, 0)

    def test_toggle_cycles(self, board):
        assert board.toggle(17).level == 1
        assert board.toggle(17).level == 0
        assert board.toggle(17).level == 1

    def test_read_with_pull_up(self, board):
        assert board.read(4, pull="up") == 1
        assert board.pin(4).mode == PinMode.INPUT

    def test_read_stops_running_pwm(self, board):
        board.set_pwm(18, 100)
        board.read(18)
        assert board.pin(18).pwm_value == 0

    def test_reserved_pin_needs_force(self, board):
        with pytest.raises(PinReservedException):
            board.switch_on(0)

    def test_reserved_pin_with_force(self, board):
        assert board.switch_on(0, force=True).level == 1

    def test_pwm_rejected_on_non_pwm_pin(self, board):
        with pytest.raises(PwmNotSupportedException):
            board.set_pwm(22, 100)

    @pytest.mark.parametrize("bad", [-1, 256, 3.5, "many"])
    def test_pwm_value_must_be_0_to_255(self, board, bad):
        with pytest.raises(InvalidPwmValueException):
            board.set_pwm(18, bad)

    def test_pwm_enable_then_disable(self, board):
        pin = board.set_pwm(18, 128)
        assert (pin.mode, pin.pwm_value, pin.level) == (PinMode.PWM, 128, 1)
        pin = board.set_pwm(18, 0)
        assert (pin.mode, pin.pwm_value, pin.level) == (PinMode.OUTPUT, 0, 0)

    def test_pwm_value_defaults_to_zero(self, board):
        assert board.set_pwm(18).pwm_value == 0

    def test_pwm_duty_update_while_running(self, board):
        board.set_pwm(18, 100)
        assert board.set_pwm(18, 200).pwm_value == 200

    def test_switch_on_stops_running_pwm(self, board):
        board.set_pwm(18, 100)
        pin = board.switch_on(18)
        assert (pin.mode, pin.pwm_value) == (PinMode.OUTPUT, 0)

    def test_all_off(self, board):
        board.switch_on(17)
        board.set_pwm(18, 50)
        assert board.all_off() == [17, 18]
        assert board.pin(18).level == 0

    def test_all_off_skips_reserved_without_force(self, board):
        board.switch_on(0, force=True)
        assert 0 not in board.all_off()
        assert 0 in board.all_off(force=True)

    def test_release_stops_pwm(self, board):
        board.set_pwm(18, 50)
        board.release()
        assert board.pin(18).pwm_value == 0


class TestExceptionMessages:
    def test_pin_not_found(self):
        assert "BCM99" in str(PinNotFoundException(99))

    def test_pin_reserved(self):
        assert "reserved" in str(PinReservedException(0, "EEPROM"))

    def test_pwm_not_supported_lists_pins(self):
        assert "BCM12" in str(PwmNotSupportedException(22, [12]))

    def test_invalid_pwm_value(self):
        assert "0-255" in str(InvalidPwmValueException(300))
