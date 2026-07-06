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
import pathlib

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, RichLog

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


class BrewCheckerTUI(App):
    TITLE = "brew-checker"
    SUB_TITLE = "cask ⇄ Applications"

    CSS = """
    #main { height: 1fr; }
    #table { width: 3fr; border: round $primary; }
    #log { width: 2fr; border: round $secondary; padding: 0 1; }
    ConfirmScreen { align: center middle; }
    #dialog {
        width: 70; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    #dialog-title { text-style: bold; margin-bottom: 1; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #dialog-buttons Button { margin: 0 2; }
    """

    BINDINGS = [
        Binding("space", "toggle_select", "Select"),
        Binding("r", "reinstall", "Reinstall"),
        Binding("d", "drop", "Drop"),
        Binding("i", "install", "Install"),
        Binding("f5", "rescan", "Rescan"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.selected: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", wrap=True, markup=True, highlight=True)
        yield Footer()

    @property
    def table(self) -> DataTable:
        return self.query_one("#table", DataTable)

    @property
    def log_widget(self) -> RichLog:
        return self.query_one("#log", RichLog)

    async def on_mount(self) -> None:
        self.table.add_column(" ", key="sel", width=3)
        self.table.add_column("type", key="kind", width=10)
        self.table.add_column("name", key="name", width=30)
        self.table.add_column("detail", key="detail")
        self.log_widget.write("[dim]space[/]=select  [dim]r[/]=reinstall  "
                              "[dim]d[/]=drop  [dim]i[/]=install  "
                              "[dim]f5[/]=rescan  [dim]q[/]=quit")
        await self.refresh_state()

    async def refresh_state(self) -> None:
        self.log_widget.write("[dim]scanning…[/]")
        missing, untracked, unknown = await asyncio.to_thread(compute_state)
        self.selected.clear()
        self.table.clear()
        for token, apps in missing:
            self.table.add_row("", "[red]MISSING[/]", token,
                               f"[dim]→ {', '.join(apps)}[/]", key=f"m:{token}")
        for app in untracked:
            self.table.add_row("", "[yellow]UNTRACKED[/]", app, "", key=f"u:{app}")
        note = f"[b]{len(missing)}[/] missing · [b]{len(untracked)}[/] untracked"
        if unknown:
            note += f" · {len(unknown)} uninspectable"
        self.log_widget.write(note)
        self.sub_title = note

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
        for cmd in cmds:
            await self._stream(cmd)
        await self.refresh_state()

    @work(exclusive=True)
    async def _install_untracked(self) -> None:
        apps = self._selected("u:")
        if not apps:
            self.notify("Select one or more UNTRACKED apps first (space).",
                        severity="warning")
            return
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
                await self._stream(["brew", "install", "--cask", top])
            else:
                self.log_widget.write("[dim]skipped[/]")
        await self.refresh_state()

    def action_rescan(self) -> None:
        self.run_worker(self.refresh_state(), exclusive=True)

    # --- command runner ----------------------------------------------------
    async def _stream(self, cmd: list[str]) -> None:
        self.log_widget.write(f"[b]$ {' '.join(cmd)}[/]")
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=STDOUT)
        except FileNotFoundError:
            self.log_widget.write("[red]brew not found on PATH[/]")
            return
        assert proc.stdout is not None
        async for raw in proc.stdout:
            self.log_widget.write(raw.decode(errors="replace").rstrip())
        rc = await proc.wait()
        colour = "green" if rc == 0 else "red"
        self.log_widget.write(f"[{colour}][exit {rc}][/]")


def main() -> None:
    BrewCheckerTUI().run()


if __name__ == "__main__":
    main()
