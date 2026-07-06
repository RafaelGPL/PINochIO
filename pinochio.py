"""PINochIO — alias module so `import pinochio` works alongside `import gpioctl`.

The whole public API lives in gpioctl; this module re-exports it under the
package's marquee name:

    import pinochio
    pinochio.on(17)
    pinochio.pwm(18, 128)
    pinochio.usage("import")
"""
from gpioctl import *                    # noqa: F401,F403
from gpioctl import __version__, main    # noqa: F401
