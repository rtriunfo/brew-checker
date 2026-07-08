#!/usr/bin/env python3
"""
brew-checker-tui — interactive companion to brew-checker.py.

Same reconciliation (MISSING casks / UNTRACKED apps) as the read-only script,
but in a Textual TUI where you can select rows and act on them:

  MISSING   apps  →  [r] reinstall the cask   |  [d] drop (uninstall) the cask
  UNTRACKED apps  →  [i] search for a matching cask and install it

The detection engine is imported from the original brew-checker.py (left
untouched) — this file only adds the UI and the state-changing actions. Every
brew command is shown and confirmed before it runs, and its output streams into
the log pane on the right.
"""

import asyncio
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, OptionList, RichLog
from textual.widgets.option_list import Option

# --- reuse the read-only engine from the original script, without touching it ---
_CORE_PATH = pathlib.Path(__file__).with_name("brew-checker.py")
_spec = importlib.util.spec_from_file_location("brew_checker_core", _CORE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise SystemExit(f"cannot locate engine at {_CORE_PATH}")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

PIPE = asyncio.subprocess.PIPE
STDOUT = asyncio.subprocess.STDOUT


def compute_state():
    """Run the engine and return (missing, untracked, unknown). Blocking."""
    tokens = core.installed_casks()
    mapping, unknown = core.cask_apps(tokens)
    missing, owned = [], set()
    for token in tokens:
        if token in unknown:
            continue
        apps = mapping.get(token, [])
        if not apps:
            continue
        owned.update(apps)
        gone = [a for a in apps if not core.app_exists(a)]
        if gone:
            missing.append((token, gone))
    untracked = sorted(core.present_apps() - owned)
    return sorted(missing), untracked, unknown


def compute_upgrades(kind="cask", greedy=False):
    """Return rows of (name, installed_version, latest_or_None, is_outdated).

    `kind` is "cask" or "formula". Outdated packages come first. `latest` is only
    known for outdated ones (for casks brew can't compare :latest / auto-updating
    tokens, so those stay "up to date"). `greedy` only applies to casks. Blocking.
    """
    flag = f"--{kind}"
    json_key = "casks" if kind == "cask" else "formulae"

    versions = {}
    for line in core.run(["brew", "list", flag, "--versions"]).stdout.splitlines():
        parts = line.split()
        if parts:
            versions[parts[0]] = " ".join(parts[1:]) or "—"

    cmd = ["brew", "outdated", flag, "--json=v2"]
    if greedy and kind == "cask":
        cmd.append("--greedy")
    data = json.loads(core.run(cmd).stdout)
    outdated = {p["name"]: p.get("current_version", "?") for p in data.get(json_key, [])}

    rows = []
    for name in sorted(versions):
        if name in outdated:
            rows.append((name, versions[name], outdated[name], True))
    for name in sorted(versions):
        if name not in outdated:
            rows.append((name, versions[name], None, False))
    return rows


def compute_backup_diff(path):
    """Load a backup and diff it against this machine.

    Returns (meta, backup, diff); the full backup is included so the view can show
    every item's status, not just the differences. Blocking.
    """
    backup = core.load_backup(path)
    return backup.get("meta", {}), backup, core.diff_backup(backup)


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No modal that lists the exact commands about to run."""

    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n,escape", "no", "No"),
    ]

    def __init__(self, lines, header="Run these commands?"):
        super().__init__()
        self._lines = lines
        self._header = header

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._header, id="dialog-title")
            yield Label("\n".join(f"  $ {ln}" for ln in self._lines), id="dialog-body")
            with Horizontal(id="dialog-buttons"):
                yield Button("Yes  (y)", variant="success", id="yes")
                yield Button("No  (n)", variant="error", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class BackupPickerScreen(ModalScreen[str | None]):
    """Modal list of saved backups; dismisses with the chosen file's path (or None).

    Supports multi-select deletion: press space to toggle selection on one or more
    backups, then 'd' to delete them (with confirmation). Press enter to load the
    highlighted backup.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("space", "toggle_select", "Select"),
        Binding("d", "delete", "Delete"),
    ]

    def __init__(self, entries):
        super().__init__()
        self._entries = entries  # [(path, meta, n_formulae, n_casks, n_taps), …]
        self._selected: set[int] = set()

    def _option_label(self, idx: int) -> str:
        path, meta, nf, nc, nt = self._entries[idx]
        host = meta.get("host", "?")
        date = meta.get("date", "?")
        total = nf + nc + nt
        mark = "✔ " if idx in self._selected else "  "
        return f"{mark}{host:<16} {date:<20} {nf}f {nc}c {nt}t ({total} total)"

    def _refresh_picker(self) -> None:
        picker = self.query_one("#picker", OptionList)
        picker.clear_options()
        for idx in range(len(self._entries)):
            picker.add_option(Option(self._option_label(idx)))

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Load a backup", id="dialog-title")
            options = [Option(self._option_label(i)) for i in range(len(self._entries))]
            yield OptionList(*options, id="picker")

    def on_mount(self) -> None:
        self.query_one("#picker", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._entries[event.option_index][0])

    def action_toggle_select(self) -> None:
        picker = self.query_one("#picker", OptionList)
        idx = picker.highlighted
        if idx is None:
            return
        if idx in self._selected:
            self._selected.remove(idx)
        else:
            self._selected.add(idx)
        picker.replace_option_prompt_at_index(idx, self._option_label(idx))

    def action_delete(self) -> None:
        if not self._selected:
            self.notify("Select one or more backups to delete first (space).",
                        severity="warning")
            return
        # push_screen_wait requires a worker context (see _pick_backup), so the
        # confirm-and-delete flow has to run inside one, not directly in the action.
        self._delete_selected()

    @work
    async def _delete_selected(self) -> None:
        filenames = [os.path.basename(self._entries[i][0]) for i in sorted(self._selected)]
        ok = await self.app.push_screen_wait(ConfirmScreen(filenames, header="Delete these backups?"))
        if not ok:
            return
        for idx in sorted(self._selected, reverse=True):
            try:
                os.remove(self._entries[idx][0])
            except OSError as exc:
                self.notify(f"Failed to delete {os.path.basename(self._entries[idx][0])}: {exc}",
                            severity="error")
                continue
        # Remove deleted entries, keeping remaining ones in order
        surviving = [e for i, e in enumerate(self._entries) if i not in self._selected]
        self._entries = surviving
        self._selected.clear()
        if not self._entries:
            self.dismiss(None)
            return
        self._refresh_picker()

    def action_cancel(self) -> None:
        self.dismiss(None)


class BrewCheckerTUI(App):
    TITLE = "brew-checker"
    SUB_TITLE = "cask ⇄ Applications"

    CSS = """
    #main { height: 1fr; }
    #table { width: 3fr; border: round $primary; }
    #log { width: 2fr; border: round $secondary; padding: 0 1; }
    ConfirmScreen { align: center middle; }
    BackupPickerScreen { align: center middle; }
    #dialog {
        width: 80; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    #dialog-title { text-style: bold; margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #dialog-buttons Button { margin: 0 2; }
    #picker { height: auto; max-height: 20; }
    #search { display: none; dock: bottom; height: 1; }
    """

    BINDINGS = [
        Binding("v", "switch_view", "View"),
        Binding("1", "goto_casks", "Casks"),
        Binding("2", "goto_formulae", "Formulae"),
        Binding("3", "goto_reconcile", "Reconcile"),
        Binding("4", "goto_backup", "Backup"),
        Binding("slash", "search", "Search"),
        Binding("space", "toggle_select", "Select"),
        Binding("r", "reinstall", "Reinstall"),
        Binding("d", "drop", "Drop"),
        Binding("i", "install", "Install"),
        Binding("m", "toggle_missing", "±Missing"),
        Binding("u", "toggle_untracked", "±Untracked"),
        Binding("U", "upgrade", "Install/Upgrade"),
        Binding("g", "toggle_greedy", "Greedy"),
        Binding("l", "load", "Load"),
        Binding("e", "export", "Export"),
        Binding("f5", "rescan", "Rescan"),
        Binding("q", "quit", "Quit"),
    ]

    COMMAND_PALETTE_DISPLAY = "^p"

    # the ordered set of views cycled through with `v`
    _VIEWS = ["casks", "formulae", "reconcile", "backup"]
    # views where the `U` (apply) action installs/upgrades the selected rows
    _UPGRADE_VIEWS = {"casks", "formulae", "backup"}

    # actions that only make sense in one view (used to gate keys + footer)
    _RECONCILE_ONLY = {"reinstall", "drop", "install", "toggle_missing", "toggle_untracked"}

    def __init__(self, backup_path=None):
        super().__init__()
        self.selected: set[str] = set()
        self.view = "casks"                # one of _VIEWS
        self.show = {"m": True, "u": True}  # which reconcile groups are visible
        self.greedy = False                 # include auto-updating casks in outdated
        self.backup_path = backup_path      # backup file for the restore view (or None)
        self._missing: list = []
        self._untracked: list = []
        self._unknown: list = []
        self._backup_diff: dict | None = None
        self._all_rows: list[tuple[str | None, tuple[str, ...], str]] = []
        self._filter_query: str = ""
        self._filter_active: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", wrap=True, markup=True, highlight=True)
        yield Input(id="search", placeholder="filter rows…")
        yield Footer()

    @property
    def table(self) -> DataTable:
        return self.query_one("#table", DataTable)

    @property
    def log_widget(self) -> RichLog:
        return self.query_one("#log", RichLog)

    async def on_mount(self) -> None:
        self._configure_columns()
        self.log_widget.write("[dim]1[/]=casks  [dim]2[/]=formulae  [dim]3[/]=reconcile  [dim]4[/]=backup  "
                              "[dim]v[/]=cycle  [dim]/[/]=filter  [dim]space[/]=select  [dim]f5[/]=rescan  [dim]^p[/]=palette  [dim]q[/]=quit")
        await self.refresh_state()

    def check_action(self, action: str, parameters) -> bool | None:
        """Hide view-specific bindings from the footer when they don't apply."""
        if action in self._RECONCILE_ONLY and self.view != "reconcile":
            return None
        if action == "upgrade" and self.view not in self._UPGRADE_VIEWS:
            return None
        if action == "toggle_greedy" and self.view != "casks":
            return None  # greedy is a cask-only concept
        if action in ("export", "load") and self.view != "backup":
            return None  # backup store actions only belong in the backup view
        return True

    def get_system_commands(self, screen) -> list[SystemCommand]:
        yield from super().get_system_commands(screen)
        for view, label in [
            ("casks", "Casks"),
            ("formulae", "Formulae"),
            ("reconcile", "Reconcile"),
            ("backup", "Backup"),
        ]:
            yield SystemCommand(
                f"Go to: {label}",
                f"Switch to the {label} view",
                lambda v=view: self.action_goto_view(v),
            )

    def _configure_columns(self) -> None:
        self.table.clear(columns=True)
        self.table.add_column(" ", key="sel", width=3)
        if self.view == "reconcile":
            self.table.add_column("type", key="kind", width=10)
            self.table.add_column("name", key="name", width=30)
            self.table.add_column("detail", key="detail")
        elif self.view == "backup":
            self.table.add_column("status", key="status", width=10)
            self.table.add_column("kind", key="kind", width=10)
            self.table.add_column("name", key="name")
        else:
            self.table.add_column("cask" if self.view == "casks" else "formula",
                                  key="name", width=28)
            self.table.add_column("installed", key="installed", width=22)
            self.table.add_column("latest", key="latest", width=22)
            self.table.add_column("status", key="status")

    async def refresh_state(self) -> None:
        self.selected.clear()
        self.table.loading = True  # spinner overlay so the wait is visibly "working"
        try:
            if self.view == "reconcile":
                self.log_widget.write("[dim]scanning…[/]")
                self._missing, self._untracked, self._unknown = \
                    await asyncio.to_thread(compute_state)
                self._populate()
            elif self.view == "casks":
                self.log_widget.write("[dim]checking cask versions…[/]")
                rows = await asyncio.to_thread(compute_upgrades, "cask", self.greedy)
                self._populate_upgrades(rows, "c", "casks")
            elif self.view == "formulae":
                self.log_widget.write("[dim]checking formula versions…[/]")
                rows = await asyncio.to_thread(compute_upgrades, "formula")
                self._populate_upgrades(rows, "f", "formulae")
            else:  # backup
                await self._refresh_backup()
        finally:
            self.table.loading = False

    async def _refresh_backup(self) -> None:
        self.table.clear()
        if not self.backup_path:
            self._backup_diff = None
            self.sub_title = "backup — none loaded"
            saved = await asyncio.to_thread(core.list_backups)
            if saved:
                self.log_widget.write(f"[dim]{len(saved)} saved backup(s)[/] — "
                                      "opening picker ([dim]l[/] to reopen, "
                                      "[dim]e[/] to snapshot this machine).")
                self.call_after_refresh(self.action_load)
            else:
                self.log_widget.write("[yellow]no saved backups[/] — press [b]e[/] "
                                      f"to snapshot this machine into {core.BACKUP_DIR}.")
            return
        self.log_widget.write(f"[dim]loading backup {self.backup_path}…[/]")
        try:
            meta, backup, diff = await asyncio.to_thread(compute_backup_diff, self.backup_path)
        except SystemExit as exc:  # load_backup raises this on bad/missing files
            self._backup_diff = None
            self.log_widget.write(f"[red]{exc}[/]")
            self.sub_title = "backup — load failed"
            return
        self._backup_diff = diff
        self._populate_backup(meta, backup, diff)

    def _mark(self, key: str) -> str:
        return "[green]✔[/]" if key in self.selected else ""

    @staticmethod
    def _strip_markup(text: str) -> str:
        import re
        return re.sub(r"\[/?[a-zA-Z_ ]*\]", "", text)

    def _populate(self) -> None:
        """Build row data from the cached scan, honouring the visibility flags."""
        self._all_rows = []
        if self.show["m"]:
            for token, apps in self._missing:
                key = f"m:{token}"
                detail = f"[dim]→ {', '.join(apps)}[/]"
                self._all_rows.append((key, (self._mark(key), "[red]MISSING[/]", token, detail),
                                       f"{token} {' '.join(apps)} missing"))
        if self.show["u"]:
            for app in self._untracked:
                key = f"u:{app}"
                self._all_rows.append((key, (self._mark(key), "[yellow]UNTRACKED[/]", app, ""),
                                       f"{app} untracked"))

        def part(count: int, label: str, visible: bool) -> str:
            hidden = "" if visible else " [dim](hidden)[/]"
            return f"[b]{count}[/] {label}{hidden}"

        note = f"{part(len(self._missing), 'missing', self.show['m'])} · " \
               f"{part(len(self._untracked), 'untracked', self.show['u'])}"
        if self._unknown:
            note += f" · {len(self._unknown)} uninspectable"
        self.log_widget.write(note)
        self.sub_title = self._strip_markup(note)
        self._render_table()

    def _populate_upgrades(self, rows, prefix, noun) -> None:
        self._all_rows = []
        outdated = 0
        for name, installed, latest, is_outdated in rows:
            key = f"{prefix}:{name}"
            if is_outdated:
                outdated += 1
                self._all_rows.append((key, (self._mark(key), name, installed,
                                   f"[green]{latest}[/]", "[yellow]outdated[/]"),
                                   f"{name} {installed} {latest} outdated"))
            else:
                self._all_rows.append((key, (self._mark(key), name, installed,
                                   "[dim]—[/]", "[dim]up to date[/]"),
                                   f"{name} {installed} up to date"))
        note = f"[b]{outdated}[/] upgradeable · {len(rows)} {noun}"
        if self.view == "casks" and self.greedy:
            note += " [dim](greedy)[/]"
        self.log_widget.write(note)
        self.sub_title = self._strip_markup(note)
        self._render_table()

    def _populate_backup(self, meta, backup, diff) -> None:
        """Show the full backup inventory, one row per item, with its status:
        MISSING (in backup, not here — selectable+installable), INSTALLED (in both,
        info-only), or EXTRA (here, not in backup — info-only). Even a backup that
        matches this machine shows its whole list, all marked INSTALLED."""
        self._all_rows = []
        # key prefixes: formulae "bf:", casks "bc:" (taps shown but not selectable)
        prefixes = {"formulae": "bf", "casks": "bc"}
        n_installed = n_missing = n_extra = 0
        for kind in ("taps", "formulae", "casks"):
            missing, extra = diff[kind]
            missing_set = set(missing)
            singular = kind[:-1]
            # MISSING first (the actionable rows), then INSTALLED, then EXTRA.
            for name in missing:
                n_missing += 1
                if kind in prefixes:
                    key = f"{prefixes[kind]}:{name}"
                    self._all_rows.append((key, (self._mark(key), "[red]MISSING[/]",
                                       singular, name), f"{name} {singular} missing"))
                else:  # taps: shown for context, auto-added on restore
                    self._all_rows.append((None, ("", "[red]MISSING[/]", "tap", f"[dim]{name}[/]"),
                                           f"{name} tap missing"))
            for name in backup.get(kind, []):
                if name in missing_set:
                    continue
                n_installed += 1
                self._all_rows.append((None, ("", "[green]INSTALLED[/]", singular, f"[dim]{name}[/]"),
                                       f"{name} {singular} installed"))
            for name in extra:
                n_extra += 1
                self._all_rows.append((None, ("", "[yellow]EXTRA[/]", singular, f"[dim]{name}[/]"),
                                       f"{name} {singular} extra"))
        tag = f"{meta.get('host', '?')} · {meta.get('date', '?')}"
        note = (f"[green]{n_installed} installed[/] · [red]{n_missing} to install[/] · "
                f"[yellow]{n_extra} extra[/] · backup: [dim]{tag}[/]")
        self.log_widget.write(note)
        self.sub_title = f"backup — {tag}"
        self._render_table()

    def _render_table(self) -> None:
        """Clear and re-add rows from _all_rows, applying the filter if active."""
        self.table.clear()
        query = self._filter_query.lower() if self._filter_active else ""
        shown = 0
        for key, cells, searchable in self._all_rows:
            if query and query not in searchable.lower():
                continue
            self.table.add_row(*cells, key=key)
            shown += 1
        if self._filter_active and query and shown == 0:
            self.log_widget.write(f"[dim]no matches for \"{self._filter_query}\"[/]")

    # --- selection ---------------------------------------------------------
    def _current_key(self) -> str | None:
        if self.table.row_count == 0:
            return None
        cell = self.table.coordinate_to_cell_key(Coordinate(self.table.cursor_row, 0))
        return cell.row_key.value

    def action_toggle_select(self) -> None:
        key = self._current_key()
        if key is None:
            return
        if key in self.selected:
            self.selected.remove(key)
            self.table.update_cell(key, "sel", "")
        else:
            self.selected.add(key)
            self.table.update_cell(key, "sel", "[green]✔[/]")

    def _selected(self, prefix: str) -> list[str]:
        return [k.split(":", 1)[1] for k in self.selected if k.startswith(prefix)]

    # --- actions -----------------------------------------------------------
    _VIEW_SUBTITLES = {
        "reconcile": "cask ⇄ Applications",
        "casks": "cask versions & upgrades",
        "formulae": "formula versions & upgrades",
        "backup": "backup & restore",
    }

    def action_switch_view(self) -> None:
        self.view = self._VIEWS[(self._VIEWS.index(self.view) + 1) % len(self._VIEWS)]
        self._switch_to_view(self.view)

    def action_goto_reconcile(self) -> None:
        self.action_goto_view("reconcile")

    def action_goto_casks(self) -> None:
        self.action_goto_view("casks")

    def action_goto_formulae(self) -> None:
        self.action_goto_view("formulae")

    def action_goto_backup(self) -> None:
        self.action_goto_view("backup")

    def action_goto_view(self, view: str) -> None:
        if view == self.view:
            return
        self.view = view
        self._switch_to_view(view)

    def _switch_to_view(self, view: str) -> None:
        self.selected.clear()
        self._clear_filter()
        self.sub_title = self._VIEW_SUBTITLES[view]
        self._configure_columns()
        self.refresh_bindings()  # update the footer for the new view
        self.run_worker(self.refresh_state(), exclusive=True)

    # --- search / filter ---------------------------------------------------
    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()
        self._filter_active = True

    def _clear_filter(self) -> None:
        self._filter_query = ""
        self._filter_active = False
        search = self.query_one("#search", Input)
        search.value = ""
        search.display = False

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        self._filter_query = event.value
        self._render_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        # Enter: keep the filter, just unfocus and hide the bar
        event.input.display = False
        self.table.focus()

    def on_key(self, event: Key) -> None:
        search = self.query_one("#search", Input)
        if search.has_focus and event.key == "escape":
            self._clear_filter()
            self.table.focus()
            event.prevent_default()
            event.stop()

    def action_toggle_greedy(self) -> None:
        if self.view != "casks":
            return
        self.greedy = not self.greedy
        self.run_worker(self.refresh_state(), exclusive=True)

    def action_upgrade(self) -> None:
        # `U` applies the current view's action: upgrade (casks/formulae) or
        # install-from-backup (backup view).
        if self.view == "backup":
            self._run_restore()
        else:
            self._run_upgrade()

    @work(exclusive=True)
    async def _run_upgrade(self) -> None:
        prefix, flag, noun = ("c:", "--cask", "casks") if self.view == "casks" \
            else ("f:", "--formula", "formulae")
        names = self._selected(prefix)
        if not names:
            self.notify(f"Select one or more {noun} to upgrade first (space).",
                        severity="warning")
            return
        cmds = [["brew", "upgrade", flag, n] for n in names]
        ok = await self.push_screen_wait(
            ConfirmScreen([" ".join(c) for c in cmds], header="brew upgrade?"))
        if not ok:
            self.log_widget.write("[dim]cancelled[/]")
            return
        self.log_widget.write("[dim]running in terminal (handles sudo/password prompts)…[/]")
        for cmd, rc in await self._run_in_terminal(cmds):
            self._log_exit(cmd, rc)
        await self.refresh_state()

    @work(exclusive=True)
    async def _run_restore(self) -> None:
        formulae = self._selected("bf:")
        casks = self._selected("bc:")
        if not (formulae or casks):
            self.notify("Select one or more MISSING items to install first (space).",
                        severity="warning")
            return
        # Add any taps the backup needs but this machine lacks, before installing.
        cmds = [["brew", "tap", t] for t in self._backup_diff["taps"][0]]
        cmds += [["brew", "install", "--formula", n] for n in formulae]
        cmds += [["brew", "install", "--cask", n] for n in casks]
        ok = await self.push_screen_wait(
            ConfirmScreen([" ".join(c) for c in cmds], header="install from backup?"))
        if not ok:
            self.log_widget.write("[dim]cancelled[/]")
            return
        self.log_widget.write("[dim]running in terminal (handles sudo/password prompts)…[/]")
        for cmd, rc in await self._run_in_terminal(cmds):
            self._log_exit(cmd, rc)
        await self.refresh_state()

    def action_load(self) -> None:
        if self.view == "backup":
            self._pick_backup()

    @work(exclusive=True)
    async def _pick_backup(self) -> None:
        entries = await asyncio.to_thread(core.list_backups)
        if not entries:
            self.notify(f"No saved backups in {core.BACKUP_DIR} — press e to create one.",
                        severity="warning")
            return
        path = await self.push_screen_wait(BackupPickerScreen(entries))
        if path is None:
            return  # cancelled
        self.backup_path = path
        await self.refresh_state()

    def action_export(self) -> None:
        if self.view != "backup":
            return
        # Always write a fresh timestamped snapshot into the store, so backups
        # accumulate for the picker, then load it.
        path = core.default_backup_path()
        try:
            core.write_backup(core.build_backup(), path)
        except OSError as exc:
            self.log_widget.write(f"[red]export failed: {exc}[/]")
            return
        self.backup_path = path
        self.log_widget.write(f"[green]saved[/] this machine's state → [b]{path}[/]")
        self.run_worker(self.refresh_state(), exclusive=True)

    def action_reinstall(self) -> None:
        self._run_cask_action("m:", "reinstall")

    def action_drop(self) -> None:
        self._run_cask_action("m:", "uninstall")

    def action_install(self) -> None:
        self._install_untracked()

    @work(exclusive=True)
    async def _run_cask_action(self, prefix: str, verb: str) -> None:
        names = self._selected(prefix)
        if not names:
            self.notify("Select one or more MISSING casks first (space).",
                        severity="warning")
            return
        cmds = [["brew", verb, "--cask", n] for n in names]
        ok = await self.push_screen_wait(
            ConfirmScreen([" ".join(c) for c in cmds], header=f"brew {verb}?"))
        if not ok:
            self.log_widget.write("[dim]cancelled[/]")
            return
        self.log_widget.write("[dim]running in terminal (handles sudo/password prompts)…[/]")
        for cmd, rc in await self._run_in_terminal(cmds):
            self._log_exit(cmd, rc)
        await self.refresh_state()

    @work(exclusive=True)
    async def _install_untracked(self) -> None:
        apps = self._selected("u:")
        if not apps:
            self.notify("Select one or more UNTRACKED apps first (space).",
                        severity="warning")
            return
        to_install: list[list[str]] = []
        for app in apps:
            term = (app[:-4] if app.endswith(".app") else app).strip().lower().replace(" ", "-")
            self.log_widget.write(f"[b]$ brew search --cask {term}[/]")
            proc = await asyncio.create_subprocess_exec(
                "brew", "search", "--cask", term, stdout=PIPE, stderr=STDOUT)
            out = (await proc.communicate())[0].decode(errors="replace")
            self.log_widget.write(out.rstrip() or "[dim](no output)[/]")
            candidates = [ln.strip() for ln in out.splitlines()
                          if ln.strip() and not ln.startswith("==>")]
            if not candidates:
                self.log_widget.write(f"[yellow]No cask match for {app}; skipping.[/]")
                continue
            top = candidates[0]
            ok = await self.push_screen_wait(ConfirmScreen(
                [f"brew install --cask {top}"],
                header=f"Install best match for {app}?"))
            if ok:
                to_install.append(["brew", "install", "--cask", top])
            else:
                self.log_widget.write("[dim]skipped[/]")
        if to_install:
            self.log_widget.write("[dim]running installs in terminal…[/]")
            for cmd, rc in await self._run_in_terminal(to_install):
                self._log_exit(cmd, rc)
        await self.refresh_state()

    def _toggle_group(self, prefix: str) -> None:
        self.show[prefix] = not self.show[prefix]
        if not self.show[prefix]:
            # dropping now-hidden rows keeps actions from touching invisible items
            self.selected = {k for k in self.selected if not k.startswith(f"{prefix}:")}
        self._populate()

    def action_toggle_missing(self) -> None:
        self._toggle_group("m")

    def action_toggle_untracked(self) -> None:
        self._toggle_group("u")

    def action_rescan(self) -> None:
        self.run_worker(self.refresh_state(), exclusive=True)

    # --- command runner ----------------------------------------------------
    def _log_exit(self, cmd: list[str], rc: int) -> None:
        colour = "green" if rc == 0 else "red"
        self.log_widget.write(f"[b]$ {' '.join(cmd)}[/] [{colour}][exit {rc}][/]")

    def _run_batch_blocking(self, cmds: list[list[str]]) -> list[tuple[list[str], int]]:
        """Run each command with the real terminal attached, so brew's sudo/password
        prompts work. Blocking — runs off the event loop via asyncio.to_thread."""
        results: list[tuple[list[str], int]] = []
        for cmd in cmds:
            print(f"\n\033[1m$ {' '.join(cmd)}\033[0m", flush=True)
            try:
                rc = subprocess.run(cmd).returncode  # inherits stdin/stdout/stderr
            except FileNotFoundError:
                print("brew not found on PATH", flush=True)
                rc = 127
            print(f"\033[2m[exit {rc}]\033[0m", flush=True)
            results.append((cmd, rc))
        try:
            input("\n[finished — press Enter to return to brew-checker] ")
        except EOFError:
            pass
        return results

    async def _run_in_terminal(self, cmds: list[list[str]]) -> list[tuple[list[str], int]]:
        """Suspend the TUI and run brew in the real terminal, then resume.

        brew casks can invoke sudo (e.g. removing a launchctl helper), which reads
        the password straight from the terminal. Piping stdout the way the scan
        does would leave that prompt with nowhere to go and hang, so for anything
        that changes state we give brew the actual terminal instead.
        """
        if not cmds:
            return []
        try:
            with self.suspend():
                return await asyncio.to_thread(self._run_batch_blocking, cmds)
        except Exception as exc:  # e.g. SuspendNotSupported outside a real terminal
            self.log_widget.write(f"[red]could not run in terminal: {exc}[/]")
            return []


def main() -> None:
    # Optional positional arg: a backup file to load in the backup/restore view.
    backup_path = sys.argv[1] if len(sys.argv) > 1 else None
    BrewCheckerTUI(backup_path).run()


if __name__ == "__main__":
    main()
