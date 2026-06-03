#!/usr/bin/env python3
# Roblox Multi-Client Rejoin Tool
# Detects ALL installed Roblox clients and rejoins them simultaneously
# Requires: pip install rich

import subprocess, threading, time, json, os, sys, re
from datetime import datetime
from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich         import box

console = Console()

CONFIG_PATH = os.path.join(os.environ.get("HOME", "."), ".rblx_rejoin.json")
DEFAULT_CFG = {
    "place_id":    "",
    "interval":    30,
    "watchdog":    True,
    "check_sec":   15,
    "launch_wait": 5,
}

CLIENT_PATTERNS = [
    "com.roblox",
    "lunex",
    "delta",
    "bloxstrap",
    "solara",
    "arceus",
    "fluxus",
    "hydrogen",
    "executor",
]

_stop    = threading.Event()
_threads = []
_running = False

# ─── config ───────────────────────────────────────────────────────────────────

def load_cfg():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CFG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CFG.copy()

def save_cfg(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── client detection ─────────────────────────────────────────────────────────

def get_clients() -> list[dict]:
    out = subprocess.run(
        ["pm", "list", "packages"],
        capture_output=True, text=True
    ).stdout

    clients = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line.replace("package:", "").strip()
        if any(p in pkg.lower() for p in CLIENT_PATTERNS):
            label = "Roblox (official)" if pkg == "com.roblox.client" else \
                    " ".join(p.title() for p in pkg.split(".")[-2:])
            clients.append({"pkg": pkg, "label": label, "activity": None})

    # resolve launcher activity for each client to bypass app chooser
    for c in clients:
        c["activity"] = get_launcher_activity(c["pkg"])

    return clients

def get_launcher_activity(pkg: str) -> str | None:
    """
    Queries the main LAUNCHER activity of a package.
    Returns 'pkg/activity' string or None on failure.
    Uses cmd package resolve-activity first, falls back to pm dump parsing.
    """
    # method 1: cmd package resolve-activity (Android 8+)
    r = subprocess.run(
        ["cmd", "package", "resolve-activity", "--brief",
         "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.LAUNCHER",
         "-p", pkg],
        capture_output=True, text=True
    )
    for line in r.stdout.splitlines():
        line = line.strip()
        # output looks like: "pkg/com.example.Activity"
        if "/" in line and pkg in line and not line.startswith("No"):
            return line.strip()

    # method 2: pm dump parsing
    r2 = subprocess.run(["pm", "dump", pkg], capture_output=True, text=True)
    # look for the activity after MAIN/LAUNCHER block
    dump = r2.stdout
    in_main = False
    for line in dump.splitlines():
        if "android.intent.action.MAIN" in line:
            in_main = True
        if in_main and pkg + "/" in line:
            m = re.search(rf"({re.escape(pkg)}/[\w.$]+)", line)
            if m:
                return m.group(1)

    # method 3: monkey trick — get activity from logcat isn't viable,
    # fall back to None and use deeplink-only launch
    return None

# ─── android helpers ──────────────────────────────────────────────────────────

def _sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def is_running(pkg: str) -> bool:
    rc, out, _ = _sh(["pidof", pkg])
    if rc == 0 and out.strip():
        return True
    _, out, _ = _sh(["ps", "-A"])
    return pkg in out

def kill_client(pkg: str):
    rc, _, _ = _sh(["am", "force-stop", pkg])
    if rc != 0:
        _, out, _ = _sh(["pidof", pkg])
        pid = (out.strip().split() or [None])[0]
        if pid:
            _sh(["kill", "-9", pid])

def launch_client(c: dict, place_id: str):
    """
    Launch a single client directly via its resolved activity component.
    This bypasses the Android 'Open with' chooser entirely.
    If activity resolution failed, falls back to --package intent.
    """
    pkg      = c["pkg"]
    activity = c.get("activity")  # e.g. "com.lunex.delta01/com.roblox.client.ActivityProtocol"

    if place_id:
        deep = f"roblox://experiences/start?placeId={place_id}"

        if activity:
            # direct component launch — no chooser, no dialog
            rc, _, _ = _sh([
                "am", "start",
                "-n", activity,
                "-a", "android.intent.action.VIEW",
                "-d", deep,
                "-f", "0x10000000",   # FLAG_ACTIVITY_NEW_TASK
            ])
            if rc == 0:
                return

        # fallback 1: --package scoped intent
        rc, _, _ = _sh([
            "am", "start",
            "--package", pkg,
            "-a", "android.intent.action.VIEW",
            "-d", deep,
            "-f", "0x10000000",
        ])
        if rc == 0:
            return

        # fallback 2: launch main activity first, deeplink second
        if activity:
            _sh(["am", "start", "-n", activity, "-f", "0x10000000"])
            time.sleep(2)
            _sh(["am", "start", "--package", pkg,
                 "-a", "android.intent.action.VIEW", "-d", deep])
    else:
        # no place id — just open the app
        if activity:
            _sh(["am", "start", "-n", activity, "-f", "0x10000000"])
        else:
            _sh(["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"])

# ─── rejoin logic ─────────────────────────────────────────────────────────────

def rejoin_all(clients: list[dict], cfg: dict, source: str = ""):
    if not clients:
        console.print("[bold red]No Roblox clients found![/bold red]")
        return

    ts  = datetime.now().strftime("%H:%M:%S")
    tag = f"[dim][{source}][/dim] " if source else ""

    for c in clients:
        console.print(f"[dim]{ts}[/dim] {tag}[yellow]↓[/yellow] Killing {c['label']}...")
        kill_client(c["pkg"])

    time.sleep(cfg["launch_wait"])

    launch_threads = []
    for c in clients:
        console.print(
            f"[dim]{ts}[/dim] {tag}[green]↑[/green] Launching [bold]{c['label']}[/bold]"
            f"{' → place ' + cfg['place_id'] if cfg['place_id'] else ''}..."
        )
        t = threading.Thread(target=launch_client, args=(c, cfg["place_id"]), daemon=True)
        t.start()
        launch_threads.append(t)
        time.sleep(0.8)

    for t in launch_threads:
        t.join(timeout=10)

    console.print(f"[dim]{ts}[/dim] {tag}[bold green]✓[/bold green] All clients launched.")

# ─── background workers ───────────────────────────────────────────────────────

def timer_worker(cfg):
    ivl = cfg["interval"] * 60
    while not _stop.wait(ivl):
        clients = get_clients()
        rejoin_all(clients, cfg, "Timer")

def watchdog_worker(cfg):
    time.sleep(12)
    while not _stop.is_set():
        clients = get_clients()
        dead = [c for c in clients if not is_running(c["pkg"])]
        if dead:
            names = ", ".join(c["label"] for c in dead)
            console.print(f"\n[bold red][!][/bold red] [Watchdog] Dead: {names} — restarting all...")
            rejoin_all(clients, cfg, "Watchdog")
            time.sleep(12)
        _stop.wait(cfg["check_sec"])

# ─── ui helpers ───────────────────────────────────────────────────────────────

def clear():
    os.system("clear")

def header():
    console.print(Panel(
        "[bold white]Roblox Multi-Client Rejoin Tool[/bold white]\n"
        "[dim]github.com/d1amis/d1scripts[/dim]",
        border_style="bright_blue",
        expand=False
    ))

def clients_table(clients: list[dict]):
    tbl = Table(box=box.ROUNDED, border_style="cyan", show_header=True)
    tbl.add_column("#",        style="bold yellow", width=4)
    tbl.add_column("Client",   style="bold white")
    tbl.add_column("Package",  style="dim")
    tbl.add_column("Activity", style="dim", no_wrap=False)
    tbl.add_column("Status",   width=12)
    for i, c in enumerate(clients, 1):
        st  = "[bold green]running[/bold green]" if is_running(c["pkg"]) else "[dim]stopped[/dim]"
        act = c["activity"] or "[red]not resolved[/red]"
        # trim activity to just the class part for display
        act_display = act.split("/")[-1] if "/" in str(act) else act
        tbl.add_row(str(i), c["label"], c["pkg"], act_display, st)
    return tbl

def cfg_table(cfg, client_count):
    tbl = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_row("[cyan]Clients found[/cyan]", f"[bold white]{client_count}[/bold white]")
    tbl.add_row("[cyan]Place ID[/cyan]",
                f"[bold white]{cfg['place_id']}[/bold white]"
                if cfg["place_id"] else "[dim]not set (main menu)[/dim]")
    tbl.add_row("[cyan]Restart every[/cyan]", f"[bold white]{cfg['interval']} min[/bold white]")
    tbl.add_row("[cyan]Watchdog[/cyan]",
                "[bold green]ON[/bold green]" if cfg["watchdog"] else "[bold red]OFF[/bold red]")
    tbl.add_row("[cyan]Launch cooldown[/cyan]", f"[white]{cfg['launch_wait']}s[/white]")
    return tbl

def prompt(text, default=None):
    hint = f" [dim](enter = {default})[/dim]" if default is not None else ""
    console.print(f"[bold yellow]>[/bold yellow] {text}{hint}: ", end="")
    try:
        val = input()
    except (EOFError, KeyboardInterrupt):
        val = ""
    return val.strip() or (str(default) if default is not None else "")

def ask_yn(text, current=True):
    cur = "YES" if current else "NO"
    console.print(
        f"[bold yellow]>[/bold yellow] {text} "
        f"[[bold green]Y[/bold green]/[bold red]N[/bold red]] "
        f"[dim](current: {cur})[/dim]: ",
        end=""
    )
    try:
        v = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        v = ""
    if v in ("y", "yes", "1"):  return True
    if v in ("n", "no",  "0"):  return False
    return current

def pause():
    console.print("\n[dim]Press Enter...[/dim]", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass

# ─── screens ──────────────────────────────────────────────────────────────────

def screen_settings(cfg):
    while True:
        clear()
        header()
        clients = get_clients()
        console.print(Panel(cfg_table(cfg, len(clients)),
                            title="[bold]Settings[/bold]",
                            border_style="cyan", expand=False))
        console.print()
        console.print("  [bold white]1[/bold white]  Set Place ID")
        console.print("  [bold white]2[/bold white]  Set restart interval (minutes)")
        console.print("  [bold white]3[/bold white]  Toggle Watchdog")
        console.print("  [bold white]4[/bold white]  Set launch cooldown (seconds)")
        console.print("  [bold white]0[/bold white]  Back")
        console.print()
        choice = prompt("Choice")

        if choice == "1":
            console.print()
            console.print("[dim]Find Place ID in the game URL on roblox.com:[/dim]")
            console.print("[dim]roblox.com/games/[bold]HERE[/bold]/game-name[/dim]\n")
            val = prompt("Place ID", cfg["place_id"] or "")
            cfg["place_id"] = val
            save_cfg(cfg)
            console.print("[green]✓ Saved.[/green]")
            pause()

        elif choice == "2":
            console.print()
            val = prompt("Restart every X minutes", cfg["interval"])
            try:
                cfg["interval"] = int(val)
                save_cfg(cfg)
                console.print("[green]✓ Saved.[/green]")
            except ValueError:
                console.print("[red]Invalid value.[/red]")
            pause()

        elif choice == "3":
            console.print()
            cfg["watchdog"] = ask_yn("Enable watchdog?", cfg["watchdog"])
            save_cfg(cfg)
            console.print("[green]✓ Saved.[/green]")
            pause()

        elif choice == "4":
            console.print()
            val = prompt("Cooldown seconds", cfg["launch_wait"])
            try:
                cfg["launch_wait"] = int(val)
                save_cfg(cfg)
                console.print("[green]✓ Saved.[/green]")
            except ValueError:
                console.print("[red]Invalid value.[/red]")
            pause()

        elif choice == "0":
            break

def screen_running(cfg):
    global _running
    clear()
    header()
    clients = get_clients()
    console.print(Panel(cfg_table(cfg, len(clients)),
                        title="[bold green]● RUNNING[/bold green]",
                        border_style="green", expand=False))
    console.print()
    console.print(clients_table(clients))
    console.print()
    console.print("  [bold white]1[/bold white]  Manual rejoin now (all clients)")
    console.print("  [bold white]0[/bold white]  [bold red]Stop tool[/bold red]")
    console.print()
    choice = prompt("Choice")

    if choice == "1":
        console.print()
        rejoin_all(clients, cfg, "Manual")
        pause()
    elif choice == "0":
        _stop.set()
        for t in _threads:
            t.join(timeout=3)
        _threads.clear()
        _stop.clear()
        _running = False
        console.print("[yellow]Tool stopped.[/yellow]")
        pause()

def start_tool(cfg):
    global _running, _threads
    if _running:
        return
    clients = get_clients()
    if not clients:
        console.print("[bold red]No Roblox clients detected![/bold red]")
        return
    _stop.clear()
    rejoin_all(clients, cfg, "Start")
    if cfg["interval"] > 0:
        t = threading.Thread(target=timer_worker,    args=(cfg,), daemon=True)
        t.start(); _threads.append(t)
    if cfg["watchdog"]:
        t = threading.Thread(target=watchdog_worker, args=(cfg,), daemon=True)
        t.start(); _threads.append(t)
    _running = bool(_threads)

# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    global _running

    cfg = load_cfg()

    if not cfg["place_id"]:
        clear()
        header()
        console.print(Panel(
            "[bold]First time setup![/bold]\n"
            "Enter the Place ID of the game you want to join.\n"
            "[dim]Leave empty to open Roblox on the main menu.[/dim]",
            border_style="yellow", expand=False
        ))
        console.print()
        console.print("[dim]Find it at: roblox.com/games/[bold]PLACE_ID[/bold]/game-name[/dim]\n")
        val = prompt("Place ID (or Enter to skip)")
        cfg["place_id"] = val
        save_cfg(cfg)

    while True:
        clients = get_clients()
        clear()
        header()

        status_line = "[bold green]● RUNNING[/bold green]" if _running else "[dim]○ Stopped[/dim]"
        console.print(Panel(cfg_table(cfg, len(clients)),
                            title=status_line,
                            border_style="green" if _running else "bright_blue",
                            expand=False))

        if clients:
            console.print(clients_table(clients))
        else:
            console.print(Panel("[bold red]No Roblox clients detected![/bold red]",
                                border_style="red", expand=False))
        console.print()

        if _running:
            console.print("  [bold white]1[/bold white]  Manage (manual rejoin / stop)")
        else:
            console.print("  [bold white]1[/bold white]  [bold green]START[/bold green]  — launch ALL clients + start tool")

        console.print("  [bold white]2[/bold white]  Settings")
        console.print("  [bold white]3[/bold white]  Refresh client list")
        console.print("  [bold white]4[/bold white]  Manual rejoin now")
        console.print("  [bold white]0[/bold white]  Exit")
        console.print()
        choice = prompt("Choice")

        if choice == "1":
            if _running:
                screen_running(cfg)
            else:
                console.print()
                start_tool(cfg)
                if _running:
                    console.print("[bold green]✓ Tool started![/bold green]")
                else:
                    console.print("[yellow]Check settings — timer and watchdog are both off.[/yellow]")
                pause()

        elif choice == "2":
            screen_settings(cfg)
            cfg = load_cfg()

        elif choice == "3":
            pass  # loop auto-refreshes

        elif choice == "4":
            console.print()
            rejoin_all(clients, cfg, "Manual")
            pause()

        elif choice == "0":
            if _running:
                _stop.set()
                for t in _threads:
                    t.join(timeout=2)
            console.print("[dim]Bye.[/dim]")
            sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _stop.set()
        console.print("\n[dim]Ctrl+C — exiting.[/dim]")
        sys.exit(0)
