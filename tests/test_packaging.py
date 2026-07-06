"""Packaging: the pinochio alias module and version metadata."""
import gpioctl
import pinochio


class TestPinochioAlias:
    def test_reexports_public_api(self):
        for name in gpioctl.__all__:
            assert getattr(pinochio, name) is getattr(gpioctl, name), name

    def test_exposes_main_and_version(self):
        assert pinochio.main is gpioctl.main
        assert pinochio.__version__ == gpioctl.__version__

    def test_version_is_semver(self):
        parts = gpioctl.__version__.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts)
