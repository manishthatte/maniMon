#!/usr/bin/env python3
"""
maniMon — RIGHT PANEL: the work.

What the machine is doing and whether it needs you, top to bottom in urgency
order:

  Attention queue · running jobs · tmux · campaign · recently finished ·
  run cost · repo and backups · other processes · services · network

NETWORK lives here rather than on the left panel: at 1440 px the machine panel
could not hold it and still be readable, and this one had the room.

Each section is a module under `sections/`, owning both the widgets it creates
and the code that fills them.

Author: Manish Jagdish Thatte
"""

import os
os.environ['GDK_BACKEND'] = 'x11'
os.environ.setdefault('DISPLAY', ':0')

import time

from .window import *                   # noqa: F401,F403
from .sections import (right_attention, right_sims, right_tmux, right_campaign,
                       right_recent, right_cost, right_repo, right_procs,
                       right_services, right_net)

# Urgency order: what needs a human first sits highest.
SECTIONS = (right_attention, right_sims, right_tmux, right_campaign,
            right_recent, right_cost, right_repo, right_procs,
            right_services, right_net)


class PanelRight(PanelWindow):
    WIDTH = 420
    ANCHOR = "RIGHT"
    WANT = {'procs', 'sims', 'tmux', 'campaign', 'recent', 'repo', 'backups',
            'services', 'journal', 'attention', 'disks', 'mem', 'gpus',
            'sysinfo', 'net', 'wan', 'sockets'}

    def build(self):
        self.title_bar("◉  maniMon  ·  WORK")
        for section in SECTIONS:
            section.build(self)
        self.box.pack_start(Gtk.Separator(), False, False, 0)
        self.lbl("foot", mt=3)

    def refresh(self, s):
        for section in SECTIONS:
            section.refresh(self, s)
        self.L("foot", f'<span font="{FXS}" foreground="{DIM}">'
                       f'  {time.strftime("%Y-%m-%d  %H:%M:%S")}  ·  '
                       f'{UPDATE_MS//1000}s refresh</span>')


if __name__ == "__main__":
    win = PanelRight()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
