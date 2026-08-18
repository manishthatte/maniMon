"""
Readers for what the privileged sensor daemon published.

`health` is imported lazily and every call is wrapped, so a machine that has
never had the daemon deployed still starts: those sections report themselves
unavailable rather than taking the panel down with an ImportError.

The default returned on failure is the *shape* the caller expects, never None
— a section that renders `{'present': False}` says "no BMC here", which is the
truth on a desktop board, while a None would be a crash one attribute access
later.
"""


def call(fn, default):
    try:
        from . import health
        return getattr(health, fn)()
    except Exception:
        return default


def lvm_volumes():  return call('lvm', [])
def bmc():          return call('bmc', {'present': False})
def smart():        return call('smart', {})
def dimms():        return call('dimms', {'present': False})
def ecc():          return call('ecc', {'present': False})
def peripherals():  return call('peripherals', [])


def gpu_metrics():
    """Keyed by card, so it lines up index-for-index with the `gpus` list."""
    return call('gpu_metrics', {})
