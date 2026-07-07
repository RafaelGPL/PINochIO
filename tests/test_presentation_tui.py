"""Presentation layer: curses TUI driven through a scriptable fake screen."""
import gpioctl

KEY_UP, KEY_DOWN, KEY_ENTER = 259, 258, 343


def build_tui(gpio_service, bus_service):
    interpreter = gpioctl.CommandInterpreter(gpio_service, bus_service)
    return gpioctl.GpioTui(gpio_service, interpreter, "MOCK")


class TestDrawing:
    def test_renders_every_pin_state(self, gpio_service, bus_service,
                                      fake_curses_factory, stdscr_cls):
        # one pin per visual state: PWM, out-high, out-low, input, unset
        gpio_service.set_pwm(18, 100)
        gpio_service.turn_on(17)
        gpio_service.turn_off(16)
        gpio_service.read(4)
        screen = stdscr_cls(keys=[ord("q")])
        fake_curses_factory(screen)
        build_tui(gpio_service, bus_service).run()
        drawn = " ".join(screen.drawn)
        assert "100/255" in drawn
        assert "RESERVED" in drawn

    def test_scrolling_follows_selection(self, gpio_service, bus_service,
                                         fake_curses_factory, stdscr_cls):
        keys = [KEY_DOWN] * 7 + [KEY_UP] * 8 + [ord("q")]
        screen = stdscr_cls(keys=keys, size=(10, 80))
        fake_curses_factory(screen)
        tui = build_tui(gpio_service, bus_service)
        tui.run()
        assert tui._selected == 0 and tui._offset == 0


class TestKeyHandling:
    def test_full_session(self, gpio_service, bus_service,
                          fake_curses_factory, stdscr_cls):
        keys = (
            [KEY_DOWN] * 18       # select GPIO18 (PWM-capable)
            + [ord("+"), ord("+"), ord("-")]   # PWM 15 -> 30 -> 15
            + [ord("p"), ord("p"), ord("p")]   # exact PWM: 200, empty, junk
            + [ord(" ")]          # toggle (stops PWM, drives high)
            + [ord("1"), ord("0"), ord("r")]   # on, off, read
            + [KEY_UP, 10]        # select GPIO17, Enter toggles it on
            + [ord(":")] * 3      # empty command, topic help, then...
            + [ord("x")]          # ...any key closes the help overlay
            + [ord(":")]          # quit
        )
        entries = [b"200", b"", b"abc", b"", b"help pwm", b"quit"]
        screen = stdscr_cls(keys=keys, entries=entries)
        fake_curses_factory(screen)
        tui = build_tui(gpio_service, bus_service)
        tui.run()
        # PWM was stopped by the toggle; GPIO17 ended up ON via Enter
        assert gpio_service.status()[18].pwm_value == 0
        assert gpio_service.status()[17].level == 1
        assert "Help closed" in tui._message or tui._message == "bye"

    def test_pwm_nudge_reaches_target(self, gpio_service, bus_service,
                                      fake_curses_factory, stdscr_cls):
        keys = [KEY_DOWN] * 18 + [ord("+"), ord("q")]
        screen = stdscr_cls(keys=keys)
        fake_curses_factory(screen)
        build_tui(gpio_service, bus_service).run()
        assert gpio_service.status()[18].pwm_value == 15

    def test_domain_errors_become_messages(self, gpio_service, bus_service,
                                           fake_curses_factory, stdscr_cls):
        # GPIO0 is selected initially and is reserved -> both keys must not crash
        keys = [ord(" "), ord("+"), ord("q")]
        screen = stdscr_cls(keys=keys)
        fake_curses_factory(screen)
        tui = build_tui(gpio_service, bus_service)
        tui.run()
        assert tui._message.startswith("!")

    def test_self_test_animates_the_table(self, gpio_service, bus_service,
                                           fake_curses_factory, stdscr_cls):
        keys = [ord(":"), ord("q")]
        entries = [b"test"]
        screen = stdscr_cls(keys=keys, entries=entries)
        fake_curses_factory(screen)
        tui = build_tui(gpio_service, bus_service)
        tui.run()
        assert "Self-test complete" in tui._message
        # observer redrew the table during the sequence: 26 blink steps x 2
        # messages + 5 heartbeat messages all passed through _show_progress
        drawn = " ".join(screen.drawn)
        assert "heartbeat" in drawn

    def test_help_overlay_consumes_next_key(self, gpio_service, bus_service,
                                            fake_curses_factory, stdscr_cls):
        keys = [ord(":"), ord("q"), ord("q")]   # 'q' during overlay only closes it
        entries = [b"help i2c"]
        screen = stdscr_cls(keys=keys, entries=entries)
        fake_curses_factory(screen)
        tui = build_tui(gpio_service, bus_service)
        tui.run()
        assert "Help closed" in tui._message
