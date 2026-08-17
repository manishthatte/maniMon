#!/usr/bin/env python3
"""
maniMon — RIGHT PANEL: the work.

  ⚠ Attention (click a row to dismiss) · Running simulations · tmux ·
  Campaign · Recently finished · Repo & backups · Other processes ·
  Services & journal · Network

NETWORK is a hardware section that lives here because the left panel ran out of
vertical room; thermal/power went back to the left once space allowed.

The attention queue is the panel's reason to exist: one ranked list answering
"what needs me?", aggregated from sims, tmux, backups, disks, git and the
journal. Rows are clickable — a click acknowledges that item and it does not
come back. Items also expire on their own after 48 h.

Author: Manish Jagdish Thatte
"""

import os, sys
os.environ['GDK_BACKEND'] = 'x11'
os.environ.setdefault('DISPLAY', ':0')

import time
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from panel_common import *              # noqa: F401,F403
import widgets as W
import collectors as C
from collectors import (fmt_bytes, fmt_rate, fmt_elapsed, fmt_age,
                        LAYERS)

MAX_ATTN = 12
MAX_SIMS = 4
MAX_NICS = 2
MAX_PANES = 8
MAX_RECENT = 5
MAX_PROCS = 4
MAX_COST = 5

# Run-cost figures come from a SQL join over the whole history, so they are
# recomputed on their own slow cadence rather than on every 2 s repaint.
COST_EVERY = 30.0

SEV_COL = {C.SEV_CRIT: W.CRIT, C.SEV_WARN: W.WARN,
           C.SEV_INFO: TEAL, C.SEV_OK: W.OK}


def _kv(k, v, vcol=WHITE, kf=None, vf=None):
    return (f'<span font="{kf or FXS}" foreground="{DIM}">{k}</span>'
            f'<span font="{vf or FS}" foreground="{vcol}">  {v}</span>')


class PanelRight(PanelWindow):
    WIDTH = 420
    ANCHOR = "RIGHT"
    WANT = {'procs', 'sims', 'tmux', 'campaign', 'recent', 'repo', 'backups',
            'services', 'journal', 'attention', 'disks', 'mem', 'gpus',
            'sysinfo', 'net', 'wan', 'sockets'}

    def build(self):
        self.title_bar("▶  maniMon  ·  WORK", col=CYAN)
        self._attn_keys = {}

        # 1. Attention ---------------------------------------------------------
        self.head("⚠", "ATTENTION", RED)
        self.lbl("attn_none")
        for i in range(MAX_ATTN):
            ev = Gtk.EventBox()
            ev.set_visible_window(False)
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                          Gdk.EventMask.ENTER_NOTIFY_MASK |
                          Gdk.EventMask.LEAVE_NOTIFY_MASK)
            lab = Gtk.Label()
            lab.set_xalign(0)
            lab.set_use_markup(True)
            lab.set_ellipsize(3)
            lab.set_margin_top(1)
            lab.set_margin_bottom(1)
            ev.add(lab)
            ev.connect("button-press-event", self._on_attn_click, i)
            ev.connect("enter-notify-event", self._on_attn_enter)
            ev.connect("leave-notify-event", self._on_attn_leave)
            self._lbs[f"attn{i}"] = lab
            self._wid[f"attnrow{i}"] = ev
            self.box.pack_start(ev, False, False, 0)
        self.lbl("attn_hint")

        # 2. Running simulations -----------------------------------------------
        self.head("▶", "RUNNING  SIMULATIONS", LIME)
        self.lbl("sim_none")
        for i in range(MAX_SIMS):
            b = self.vbox()
            self._wid[f"simbox{i}"] = b
            self.lbl(f"sim{i}_a", c=b)
            self.lbl(f"sim{i}_b", c=b)
            self.wid(f"sim{i}_bar", W.Gauge(height=6), c=b)

        # 3. tmux ---------------------------------------------------------------
        self.head("▣", "TMUX  SESSIONS", GREEN)
        self.lbl("tmux_none")
        for i in range(MAX_PANES):
            self.lbl(f"tmux{i}_a")
            self.lbl(f"tmux{i}_b")

        # 4. Campaign -----------------------------------------------------------
        self.head("◈", "PHASE 3  CAMPAIGN", PURPLE)
        self.lbl("camp_hdr")
        self.wid("camp_legend", Gtk.DrawingArea())
        self._wid["camp_legend"].set_size_request(-1, 14)
        self._wid["camp_legend"].connect("draw", self._draw_camp_legend)
        for i, lay in enumerate(LAYERS):
            row = self.hbox(4)
            self._wid[f"camprow{i}"] = row
            l = Gtk.Label()
            l.set_markup("")
            l.set_xalign(0)
            l.set_size_request(26, -1)
            self._lbs[f"camp{i}_l"] = l
            row.pack_start(l, False, False, 0)
            bar = W.StackBar(height=9)
            self._wid[f"camp{i}_bar"] = bar
            row.pack_start(bar, True, True, 0)
            v = Gtk.Label()
            v.set_xalign(1)
            v.set_size_request(84, -1)
            self._lbs[f"camp{i}_v"] = v
            row.pack_start(v, False, False, 0)
        self.lbl("camp_foot")

        # 5. Recently finished ---------------------------------------------------
        self.head("✓", "RECENTLY  FINISHED  (24 h)", TEAL)
        self.lbl("recent_none")
        for i in range(MAX_RECENT):
            self.lbl(f"recent{i}")

        # 5b. What runs actually cost ---------------------------------------------
        self.head("◔", "RUN  COST  (7 d)", GOLD)
        self.lbl("cost_none")
        self.lbl("cost_hdr")
        for i in range(MAX_COST):
            self.lbl(f"cost{i}")
        self.lbl("cost_foot")

        # 6. Repo & backups -------------------------------------------------------
        self.head("⎇", "REPO  &  BACKUPS", ROSE)
        self.lbl("git1")
        self.lbl("git2")
        for i in range(2):
            self.lbl(f"bkp{i}")

        # 7. Other processes -------------------------------------------------------
        self.head("≡", "OTHER  PROCESSES", GOLD)
        self.lbl("proc_hdr")
        for i in range(MAX_PROCS):
            self.lbl(f"proc{i}")

        # 8. Services & journal ----------------------------------------------------
        self.head("◆", "SERVICES  &  JOURNAL", CYAN)
        self.lbl("svc_row")
        self.lbl("jrn_row")
        for i in range(3):
            self.lbl(f"jrn{i}")

        # 9. Network  (relocated from the left panel — no room there at 1440px)
        self.head("◎", "NETWORK", TEAL)
        for i in range(MAX_NICS):
            n = self.vbox()
            self._wid[f"nicbox{i}"] = n
            self.lbl(f"nic{i}_hdr", c=n)
            self.lbl(f"nic{i}_ip", c=n)
            self.wid(f"nic{i}_spark", W.DualSpark(height=22), c=n)
            self.lbl(f"nic{i}_rate", c=n)
        self.lbl("net_wan", mt=2)

        self.box.pack_start(Gtk.Separator(), False, False, 0)
        self.lbl("foot", mt=3)

    # ── Attention interaction ────────────────────────────────────────────────
    def _on_attn_click(self, widget, event, idx):
        key = self._attn_keys.get(idx)
        if key:
            C.acknowledge(key)
            with self._lock:
                snap = dict(self.snap)
            snap['attention'] = C.attention(snap)
            with self._lock:
                self.snap = snap
            self.refresh(snap)
        return True

    def _on_attn_enter(self, widget, event):
        win = widget.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))
        return False

    def _on_attn_leave(self, widget, event):
        win = widget.get_window()
        if win:
            win.set_cursor(None)
        return False

    def _draw_camp_legend(self, widget, cr):
        W.legend(cr, 26, widget.get_allocation().height / 2, [
            (W.CAT[0], "confirmed"), (W.CAT[1], "partial"), (None, "pending"),
        ])
        return True

    # ── Refresh ──────────────────────────────────────────────────────────────
    def refresh(self, s):
        self._attention(s)
        self._sims(s)
        self._tmux(s)
        self._campaign(s)
        self._recent(s)
        self._cost(s)
        self._repo(s)
        self._procs(s)
        self._services(s)
        self._net(s)
        self.L("foot", f'<span font="{FXS}" foreground="{DIM}">'
                       f'  {time.strftime("%Y-%m-%d  %H:%M:%S")}  ·  '
                       f'{UPDATE_MS//1000}s refresh</span>')

    # 1 -------------------------------------------------------------------------
    def _attention(self, s):
        items = s.get('attention') or []
        self.vis("attn_none", not items)
        self.vis("attn_hint", bool(items))
        if not items:
            self.L("attn_none",
                   f'<span font="{FATB}" foreground="{W.OK}">✓  nothing needs you</span>')
        else:
            crit = sum(1 for i in items if i['sev'] == C.SEV_CRIT)
            self.L("attn_hint",
                   f'<span font="{FXS}" foreground="{DIM}">'
                   f'  click a row to dismiss it'
                   + (f'   ·   {crit} critical' if crit else '') + '</span>')
        self._attn_keys = {}
        for i in range(MAX_ATTN):
            if i < len(items):
                it = items[i]
                col = SEV_COL.get(it['sev'], WHITE)
                self._attn_keys[i] = it['key'] if it['ackable'] else None
                mark = '' if it['ackable'] else \
                       f'<span font="{FXS}" foreground="{DIM}">  ·</span>'
                self.L(f"attn{i}",
                       f'<span font="{FATB}" foreground="{col}">{it["icon"]}  </span>'
                       f'<span font="{FATT}" foreground="{WHITE}">{it["text"]}</span>'
                       + mark)
                self.vis(f"attnrow{i}", True)
            else:
                self.vis(f"attnrow{i}", False)
        if len(items) > MAX_ATTN:
            self.L("attn_hint",
                   f'<span font="{FXS}" foreground="{DIM}">  click to dismiss  ·  '
                   f'+{len(items)-MAX_ATTN} more not shown</span>')

    # 2 -------------------------------------------------------------------------
    def _sims(self, s):
        sims = s.get('sims') or []
        self.vis("sim_none", not sims)
        if not sims:
            self.L("sim_none",
                   f'<span font="{FS}" foreground="{DIM}">  no simulation running</span>')
        for i in range(MAX_SIMS):
            box = self._wid.get(f"simbox{i}")
            if i >= len(sims):
                if box:
                    box.set_visible(False)
                    box.set_no_show_all(True)
                continue
            if box:
                box.set_visible(True)
                box.set_no_show_all(False)
            sim = sims[i]
            self.L(f"sim{i}_a",
                   f'<span font="{FB}" foreground="{LIME}">{sim["sim_id"][:16]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {sim["comm"][:10]}'
                   f'  pid {sim["pid"]}</span>'
                   f'<span font="{FS}" foreground="{W.INK}">   {sim["cpu"]:.0f}%</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {fmt_bytes(sim["rss"])}'
                   f'  {sim["threads"]}t</span>')
            eta = (f'  ETA {fmt_elapsed(sim["eta"])}' if sim.get('eta') else '')
            prog = sim.get('progress') or '—'
            self.L(f"sim{i}_b",
                   f'<span font="{FXS}" foreground="{TEAL}">  {fmt_elapsed(sim["elapsed"])}'
                   f'{eta}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">   {prog[:44]}</span>')
            bar = self._wid.get(f"sim{i}_bar")
            if bar:
                if sim.get('frac'):
                    bar.set(sim['frac'] * 100, W.CAT[0])
                    bar.set_visible(True)
                else:
                    bar.set(0, W.CAT[0])

    # 3 -------------------------------------------------------------------------
    def _tmux(self, s):
        panes = s.get('tmux') or []
        self.vis("tmux_none", not panes)
        if not panes:
            self.L("tmux_none",
                   f'<span font="{FS}" foreground="{DIM}">  no tmux server running</span>')
        procs = {p['pid']: p for p in (s.get('procs') or [])}
        for i in range(MAX_PANES):
            if i >= len(panes):
                self.vis(f"tmux{i}_a", False)
                self.vis(f"tmux{i}_b", False)
                continue
            p = panes[i]
            col = W.CRIT if p['dead'] else (LIME if p['active'] else GREEN)
            mark = '✗' if p['dead'] else ('▸' if p['attached'] else '·')
            pr = procs.get(p['pid'])
            cpu = f'  {pr["cpu"]:.0f}%' if pr else ''
            self.L(f"tmux{i}_a",
                   f'<span font="{FS}" foreground="{col}">{mark}  </span>'
                   f'<span font="{FS}" foreground="{WHITE}">{p["session"]}:'
                   f'{p["window_name"][:14]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {p["cmd"][:12]}{cpu}</span>')
            self.L(f"tmux{i}_b",
                   f'<span font="{FXS}" foreground="{DIM}">     {p["last_line"][:52]}</span>')
            self.vis(f"tmux{i}_a", True)
            self.vis(f"tmux{i}_b", bool(p['last_line']))

    # 4 -------------------------------------------------------------------------
    def _campaign(self, s):
        c = s.get('campaign') or {}
        if not c:
            return
        scripts = max(c.get('scripts', 0), 1)
        conf, part = c.get('confirmed', 0), c.get('partial', 0)
        self.L("camp_hdr",
               f'<span font="{FB}" foreground="{W.INK}">{conf}</span>'
               f'<span font="{FS}" foreground="{DIM}"> / {scripts} confirmed</span>'
               f'<span font="{FS}" foreground="{W.CAT[1]}">   {part} partial</span>'
               f'<span font="{FS}" foreground="{DIM}">  {c.get("pending",0)} pend</span>'
               f'<span font="{FXS}" foreground="{DIM}">  ·  {c.get("ran",0)} ran</span>')
        for i, lay in enumerate(LAYERS):
            row = next((l for l in c.get('layers', []) if l['layer'] == lay), None)
            if not row or not row['scripts']:
                self._wid[f"camprow{i}"].set_visible(False)
                self._wid[f"camprow{i}"].set_no_show_all(True)
                continue
            self._wid[f"camprow{i}"].set_visible(True)
            self._wid[f"camprow{i}"].set_no_show_all(False)
            n = row['scripts']
            self._lbs[f"camp{i}_l"].set_markup(
                f'<span font="{FXS}" foreground="{DIM}">{lay}</span>')
            self._wid[f"camp{i}_bar"].set_segments([
                (row['confirmed'] / n, W.CAT[0]),
                (row['partial'] / n, W.CAT[1]),
            ])
            flag = ('<span foreground="%s">  ⚠</span>' % W.WARN) if row['unbacked'] else ''
            self._lbs[f"camp{i}_v"].set_markup(
                f'<span font="{FXS}" foreground="{DIM}">{row["confirmed"]}'
                f'+{row["partial"]}/{n}</span>{flag}')
        failed = c.get('failed', 0)
        self.L("camp_foot",
               (f'<span font="{FXS}" foreground="{W.CRIT}">  {failed} recorded '
                f'failure(s)</span>' if failed else
                f'<span font="{FXS}" foreground="{DIM}">  ⚠ = ledger credits it, '
                f'disk has nothing</span>'))

    # 5 -------------------------------------------------------------------------
    def _recent(self, s):
        rs = s.get('recent') or []
        self.vis("recent_none", not rs)
        if not rs:
            self.L("recent_none",
                   f'<span font="{FS}" foreground="{DIM}">  nothing finished today</span>')
        for i in range(MAX_RECENT):
            if i >= len(rs):
                self.vis(f"recent{i}", False)
                continue
            r = rs[i]
            ok = r['ok']
            col = W.OK if ok else W.CRIT
            mark = '✓' if ok else '✗'
            dur = f'  {fmt_elapsed(r["seconds"])}' if r.get('seconds') else ''
            self.L(f"recent{i}",
                   f'<span font="{FS}" foreground="{col}">{mark}  </span>'
                   f'<span font="{FS}" foreground="{WHITE}">{r["sim_id"][:18]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">{dur}  '
                   f'{fmt_bytes(r["size"])}  ·  {fmt_age(r["age"])}</span>')
            self.vis(f"recent{i}", True)

    # 5b ------------------------------------------------------------------------
    def _cost(self, s):
        """What each simulation actually cost.

        Exclusive figures (cpu-h, peak RSS) are the run's own. Wh and peak GPU
        junction are machine-wide over the run's window and are NOT divided
        between overlapping runs — the trailing marker says how much company a
        run had, because with CPU/GPU co-scheduling as the standing policy,
        overlap is normal rather than exceptional.
        """
        if self.runs is None or not self.runs.available:
            self.vis("cost_hdr", False)
            self.vis("cost_foot", False)
            for i in range(MAX_COST):
                self.vis(f"cost{i}", False)
            self.vis("cost_none", True)
            self.L("cost_none",
                   f'<span font="{FS}" foreground="{DIM}">  no run history — '
                   f'is manimon-metrics running?</span>')
            return

        now = time.time()
        if now - getattr(self, '_cost_at', 0) >= COST_EVERY or not hasattr(self, '_cost_rows'):
            self._cost_at = now
            try:
                rows = self.runs.list(days=7, limit=MAX_COST)
                self._cost_rows = [self.runs.enrich(r, self.stats) for r in rows]
            except Exception:
                self._cost_rows = []
        rows = self._cost_rows

        self.vis("cost_none", not rows)
        self.vis("cost_hdr", bool(rows))
        self.vis("cost_foot", bool(rows))
        if not rows:
            self.L("cost_none",
                   f'<span font="{FS}" foreground="{DIM}">  nothing has run in 7 days</span>')
            for i in range(MAX_COST):
                self.vis(f"cost{i}", False)
            return

        self.L("cost_hdr",
               f'<span font="{FXS}" foreground="{DIM}">  '
               f'{"simulation":<17}{"wall":>6}{"cpu-h":>7}{"RSS":>7}{"jc":>5}{"Wh":>7}</span>')
        for i in range(MAX_COST):
            if i >= len(rows):
                self.vis(f"cost{i}", False)
                continue
            r = rows[i]
            name = (r.get('sim_id') or r.get('comm') or '?')[:17]
            wall = fmt_elapsed(r.get('wall') or 0)
            cpuh = f"{(r.get('cpu_sec') or 0) / 3600:.2f}"
            rss = fmt_bytes(r.get('rss_max') or 0)
            jc = r.get('gpu_junction_max')
            wh = r.get('wh')
            live = r.get('ended') is None
            # Shared figures are dimmed when they were not this run's alone —
            # the number is still true of the machine, just not of this run.
            alone = (r.get('concurrent') or 1) <= 1
            shcol = WHITE if alone else DIM
            jcs = f"{jc:.0f}" if jc else "—"
            whs = f"{wh:.1f}" if wh else "—"
            self.L(f"cost{i}",
                   f'<span font="{FS}" foreground="{LIME if live else WHITE}">'
                   f'  {name:<17}</span>'
                   f'<span font="{FXS}" foreground="{TEAL}">{wall:>6}</span>'
                   f'<span font="{FXS}" foreground="{W.INK}">{cpuh:>7}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">{rss:>7}</span>'
                   f'<span font="{FXS}" foreground="{shcol}">{jcs:>5}{whs:>7}</span>'
                   + (f'<span font="{FXS}" foreground="{DIM}">  ▶</span>' if live else '')
                   + ('' if alone else f'<span font="{FXS}" foreground="{DIM}"> +{r["concurrent"]-1}</span>'))
            self.vis(f"cost{i}", True)
        self.L("cost_foot",
               f'<span font="{FXS}" foreground="{DIM}">  cpu-h and RSS are the '
               f'run\'s own · jc/Wh are machine-wide, not split (+n = shared)</span>')

    # 6 -------------------------------------------------------------------------
    def _repo(self, s):
        g = s.get('repo') or {}
        dcol = W.WARN if g.get('dirty') else W.OK
        ucol = W.WARN if g.get('unpushed') else W.OK
        self.L("git1",
               f'<span font="{FS}" foreground="{DIM}">⎇ </span>'
               f'<span font="{FB}" foreground="{CYAN}">{g.get("branch","?")}</span>'
               + _kv("   dirty", f'{g.get("dirty",0)}', dcol)
               + _kv("   unpushed", f'{g.get("unpushed",0)}', ucol))
        self.L("git2",
               f'<span font="{FXS}" foreground="{DIM}">  {g.get("last_hash","")}  '
               f'{g.get("last_subject","")}  ·  {g.get("last_when","")}</span>')
        bks = s.get('backups') or []
        for i in range(2):
            if i >= len(bks):
                self.vis(f"bkp{i}", False)
                continue
            b = bks[i]
            if not b['mounted']:
                col, txt = W.WARN, 'not mounted'
            elif b['ok'] is False:
                col, txt = W.CRIT, 'FAILED'
            elif b['stale']:
                col, txt = W.WARN, f'stale · {fmt_age(b["age"])}'
            else:
                col, txt = W.OK, f'{fmt_age(b["age"])}'
            self.L(f"bkp{i}",
                   f'<span font="{FS}" foreground="{col}">⏏ </span>'
                   f'<span font="{FS}" foreground="{WHITE}">{b["label"]}</span>'
                   f'<span font="{FXS}" foreground="{col}">  {txt}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {b["duration"]}</span>')
            self.vis(f"bkp{i}", True)

    # 7 -------------------------------------------------------------------------
    def _procs(self, s):
        procs = s.get('procs') or []
        sim_pids = {x['pid'] for x in (s.get('sims') or [])}
        others = [p for p in procs
                  if p['pid'] not in sim_pids and p['elapsed'] >= 60
                  and p['cpu'] >= 0.5][:MAX_PROCS]
        self.L("proc_hdr",
               f'<span font="{FXS}" foreground="{DIM}">'
               f'    PID   CPU%      RSS   elapsed  command</span>')
        for i in range(MAX_PROCS):
            if i >= len(others):
                self.vis(f"proc{i}", False)
                continue
            p = others[i]
            ccol = W.CRIT if p['cpu'] > 200 else (W.WARN if p['cpu'] > 80 else WHITE)
            self.L(f"proc{i}",
                   f'<span font="{FXS}">'
                   f'<span foreground="{DIM}">{p["pid"]:>7}</span>'
                   f'<span foreground="{ccol}"> {p["cpu"]:6.1f}</span>'
                   f'<span foreground="{DIM}"> {fmt_bytes(p["rss"]):>8}</span>'
                   f'<span foreground="{TEAL}"> {fmt_elapsed(p["elapsed"]):>9}</span>'
                   f'  <span foreground="{WHITE}">{p["comm"][:14]}</span></span>')
            self.vis(f"proc{i}", True)

    # 8 -------------------------------------------------------------------------
    def _services(self, s):
        svcs = s.get('services') or {}
        chunks = []
        for name, state in svcs.items():
            if state == 'active':
                dot, col = '●', W.OK
            elif state in ('inactive', 'dead'):
                dot, col = '○', DIM
            else:
                dot, col = '●', W.CRIT
            chunks.append(f'<span foreground="{col}">{dot}</span>'
                          f'<span foreground="{DIM}">{name[:5]}</span>')
        self.L("svc_row", f'<span font="{FXS}">' + '  '.join(chunks) + '</span>')
        j = s.get('journal') or {}
        e, w = j.get('errors', 0), j.get('warnings', 0)
        self.L("jrn_row",
               f'<span font="{FS}" foreground="{W.CRIT if e else DIM}">{e} err</span>'
               f'<span font="{FS}" foreground="{DIM}">  ·  </span>'
               f'<span font="{FS}" foreground="{W.WARN if w else DIM}">{w} warn</span>'
               f'<span font="{FXS}" foreground="{DIM}">  this boot</span>')
        rec = j.get('recent') or []
        for i in range(3):
            if i < len(rec):
                self.L(f"jrn{i}",
                       f'<span font="{FXS}" foreground="{DIM}">  {rec[i][:50]}</span>')
                self.vis(f"jrn{i}", True)
            else:
                self.vis(f"jrn{i}", False)


    # 9 ------------------------------------------------------------------------
    def _net(self, s):
        nics = [n for n in (s.get('net') or [])
                if (n['up'] and n['ipv4']) or n['rx_bps'] > 0]
        if not nics:
            nics = [n for n in (s.get('net') or []) if n['up']][:1]
        for i in range(MAX_NICS):
            box = self._wid.get(f"nicbox{i}")
            if i >= len(nics):
                if box:
                    box.set_visible(False)
                    box.set_no_show_all(True)
                continue
            if box:
                box.set_visible(True)
                box.set_no_show_all(False)
            n = nics[i]
            scol = W.OK if n['up'] else DIM
            spd = f'{n["speed_mbps"]} Mb/s' if n['speed_mbps'] else '—'
            t = n.get('temp')
            self.L(f"nic{i}_hdr",
                   f'<span font="{FB}" foreground="{TEAL}">{n["iface"][:16]}</span>'
                   f'<span font="{FXS}" foreground="{scol}">  {n["state"]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {spd}</span>'
                   + (f'<span font="{FXS}" foreground="{W.temp_color(t,75,90)}">'
                      f'  {t:.0f}°C</span>' if t else ''))
            self.L(f"nic{i}_ip",
                   f'<span font="{FXS}" foreground="{DIM}">  {n["ipv4"] or "no address"}'
                   f'   {(n["ipv6"] or "")[:24]}</span>')
            sp = self._wid.get(f"nic{i}_spark")
            if sp:
                sp.push(n['rx_bps'], n['tx_bps'])
            ecol = W.WARN if (n['errors'] + n['dropped']) else DIM
            self.L(f"nic{i}_rate",
                   f'<span font="{FXS}" foreground="{W.CAT[0]}">  ↓ {fmt_rate(n["rx_bps"])}</span>'
                   f'<span font="{FXS}" foreground="{W.CAT[1]}">   ↑ {fmt_rate(n["tx_bps"])}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">   Σ {fmt_bytes(n["rx_total"])}'
                   f'/{fmt_bytes(n["tx_total"])}</span>'
                   f'<span font="{FXS}" foreground="{ecol}">   e{n["errors"]}'
                   f'/d{n["dropped"]}</span>')

        wan = s.get('wan') or {}
        sk = s.get('sockets') or {}
        if wan.get('up'):
            ms = wan.get('ms') or 0
            self.L("net_wan",
                   _kv("WAN", f'{ms:.0f} ms', W.OK if ms < 60 else W.WARN) +
                   _kv("   tcp", f'{sk.get("tcp",0)}') +
                   _kv("  listen", f'{sk.get("listening",0)}'))
        else:
            self.L("net_wan",
                   f'<span font="{FS}" foreground="{W.CRIT}">WAN  unreachable</span>')


if __name__ == "__main__":
    win = PanelRight()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
