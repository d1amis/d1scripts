#!/usr/bin/env python3
# Roblox Rejoin Tool — interaktywne menu
# Wymaga: pip install rich

import subprocess, threading, time, json, os, sys
from datetime import datetime
from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich.text    import Text
from rich         import box

console = Console()

ROBLOX_PKG  = "com.roblox.client"
CONFIG_PATH = os.path.join(os.environ.get("HOME", "."), ".rblx_rejoin.json")
DEFAULT_CFG = {
    "place_id":      "",
    "interval":      30,
    "watchdog":      True,
    "check_sec":     15,
    "launch_wait":   5,
}

_stop      = threading.Event()
_threads   = []
_running   = False

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

# ─── android helpers ──────────────────────────────────────────────────────────

def _sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def is_running():
    rc, out, _ = _sh(["pidof", ROBLOX_PKG])
    if rc == 0 and out.strip():
        return True
    _, out, _ = _sh(["ps", "-A"])
    return ROBLOX_PKG in out

def kill_roblox():
    rc, _, _ = _sh(["am", "force-stop", ROBLOX_PKG])
    if rc != 0:
        _, out, _ = _sh(["pidof", ROBLOX_PKG])
        pid = (out.strip().split() or [None])[0]
        if pid:
            _sh(["kill", "-9", pid])

def launch_roblox(place_id):
    if place_id:
        deep = f"roblox://experiences/start?placeId={place_id}"
        rc, _, _ = _sh(["am", "start", "-a", "android.intent.action.VIEW", "-d", deep])
        if rc != 0:
            _sh(["termux-open-url", deep])
    else:
        _sh(["am", "start", "-n", f"{ROBLOX_PKG}/.startup.ActivityRouter"])

def do_rejoin(cfg, source=""):
    ts  = datetime.now().strftime("%H:%M:%S")
    tag = f"[dim][{source}][/dim] " if source else ""
    console.print(f"[dim]{ts}[/dim] {tag}[yellow]↓ Kill...[/yellow]")
    kill_roblox()
    time.sleep(cfg["launch_wait"])
    pid = cfg["place_id"]
    console.print(f"[dim]{ts}[/dim] {tag}[green]↑ Launch"
                  f"{' place ' + pid if pid else ' (main menu)'}[/green]")
    launch_roblox(pid)

# ─── worker threads ───────────────────────────────────────────────────────────

def timer_worker(cfg):
    ivl = cfg["interval"] * 60
    while not _stop.wait(ivl):
        do_rejoin(cfg, "Timer")

def watchdog_worker(cfg):
    time.sleep(12)
    while not _stop.is_set():
        if not is_running():
            console.print(f"\n[bold red][!][/bold red] [Watchdog] Roblox zdechl — restart...")
            do_rejoin(cfg, "Watchdog")
            time.sleep(12)
        _stop.wait(cfg["check_sec"])

# ─── display helpers ──────────────────────────────────────────────────────────

def clear():
    os.system("clear")

def header():
    console.print(Panel(
        "[bold white]Roblox Rejoin Tool[/bold white]\n"
        "[dim]github.com/d1amis/d1scripts[/dim]",
        border_style="bright_blue",
        expand=False
    ))

def cfg_summary(cfg):
    tbl = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_row("[cyan]Place ID[/cyan]",
                f"[bold white]{cfg['place_id']}[/bold white]"
                if cfg["place_id"] else "[dim]nie ustawiony (main menu)[/dim]")
    tbl.add_row("[cyan]Interval[/cyan]",
                f"[bold white]{cfg['interval']} min[/bold white]")
    tbl.add_row("[cyan]Watchdog[/cyan]",
                "[bold green]ON[/bold green]"
                if cfg["watchdog"] else "[bold red]OFF[/bold red]")
    tbl.add_row("[cyan]Cooldown launch[/cyan]",
                f"[white]{cfg['launch_wait']}s[/white]")
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
    cur = "TAK" if current else "NIE"
    console.print(
        f"[bold yellow]>[/bold yellow] {text} "
        f"[[bold green]T[/bold green]/[bold red]N[/bold red]] "
        f"[dim](aktualnie: {cur})[/dim]: ",
        end=""
    )
    try:
        v = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        v = ""
    if v in ("t", "y", "tak", "yes", "1"):
        return True
    if v in ("n", "nie", "no", "0"):
        return False
    return current

def pause():
    console.print("\n[dim]Nacisnij Enter...[/dim]", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass

# ─── screens ──────────────────────────────────────────────────────────────────

def screen_settings(cfg):
    while True:
        clear()
        header()
        console.print(Panel(cfg_summary(cfg),
                            title="[bold]Ustawienia[/bold]",
                            border_style="cyan", expand=False))
        console.print()
        console.print("  [bold white]1[/bold white]  Ustaw Place ID gry")
        console.print("  [bold white]2[/bold white]  Ustaw interval restartu (minuty)")
        console.print("  [bold white]3[/bold white]  Wlacz / wylacz Watchdog")
        console.print("  [bold white]4[/bold white]  Ustaw cooldown po killu (sekundy)")
        console.print("  [bold white]0[/bold white]  Powrot")
        console.print()
        choice = prompt("Wybor")

        if choice == "1":
            console.print()
            console.print("[dim]Place ID znajdziesz w URL gry na roblox.com:[/dim]")
            console.print("[dim]roblox.com/games/[bold]TUTAJ[/bold]/nazwa-gry[/dim]\n")
            val = prompt("Place ID", cfg["place_id"] or "")
            cfg["place_id"] = val
            save_cfg(cfg)
            console.print("[green]✓ Zapisano.[/green]")
            pause()

        elif choice == "2":
            console.print()
            val = prompt("Ile minut miedzy restartami", cfg["interval"])
            try:
                cfg["interval"] = int(val)
                save_cfg(cfg)
                console.print("[green]✓ Zapisano.[/green]")
            except ValueError:
                console.print("[red]Zla wartosc.[/red]")
            pause()

        elif choice == "3":
            console.print()
            cfg["watchdog"] = ask_yn("Watchdog aktywny?", cfg["watchdog"])
            save_cfg(cfg)
            console.print("[green]✓ Zapisano.[/green]")
            pause()

        elif choice == "4":
            console.print()
            val = prompt("Cooldown (s)", cfg["launch_wait"])
            try:
                cfg["launch_wait"] = int(val)
                save_cfg(cfg)
                console.print("[green]✓ Zapisano.[/green]")
            except ValueError:
                console.print("[red]Zla wartosc.[/red]")
            pause()

        elif choice == "0":
            break

def screen_running(cfg):
    global _running
    clear()
    header()

    lines = []
    pid = cfg["place_id"]
    lines.append(f"  Place ID : [bold white]{pid if pid else 'main menu'}[/bold white]")
    lines.append(f"  Interval : [bold white]{cfg['interval']} min[/bold white]")
    lines.append(f"  Watchdog : {'[bold green]ON[/bold green]' if cfg['watchdog'] else '[bold red]OFF[/bold red]'}")

    console.print(Panel(
        "\n".join(lines),
        title="[bold green]● DZIALA[/bold green]",
        border_style="green",
        expand=False
    ))
    console.print()
    console.print("  [bold white]1[/bold white]  Reczny rejoin teraz")
    console.print("  [bold white]0[/bold white]  [bold red]Zatrzymaj tool[/bold red]")
    console.print()
    choice = prompt("Wybor")

    if choice == "1":
        console.print()
        do_rejoin(cfg, "Manual")
        pause()

    elif choice == "0":
        _stop.set()
        for t in _threads:
            t.join(timeout=3)
        _threads.clear()
        _stop.clear()
        _running = False
        console.print("[yellow]Tool zatrzymany.[/yellow]")
        pause()

def start_tool(cfg):
    global _running, _threads
    if _running:
        return

    _stop.clear()
    do_rejoin(cfg, "Start")

    if cfg["interval"] > 0:
        t = threading.Thread(target=timer_worker,    args=(cfg,), daemon=True)
        t.start(); _threads.append(t)

    if cfg["watchdog"]:
        t = threading.Thread(target=watchdog_worker, args=(cfg,), daemon=True)
        t.start(); _threads.append(t)

    _running = bool(_threads)

def screen_status():
    clear()
    header()
    if is_running():
        console.print(Panel("[bold green]✓ Roblox dziala[/bold green]",
                            border_style="green", expand=False))
    else:
        console.print(Panel("[bold red]✗ Roblox nie dziala[/bold red]",
                            border_style="red", expand=False))
    pause()

# ─── main menu ────────────────────────────────────────────────────────────────

def main():
    global _running

    cfg = load_cfg()
    if not cfg["place_id"]:
        clear()
        header()
        console.print(Panel(
            "[bold]Pierwsze uruchomienie![/bold]\n"
            "Wpisz Place ID gry do ktorej chcesz dolaczac.\n"
            "[dim]Zostaw puste zeby Roblox otwierал sie na glownym menu.[/dim]",
            border_style="yellow", expand=False
        ))
        console.print()
        console.print("[dim]Gdzie znalezc Place ID?[/dim]")
        console.print("[dim]roblox.com/games/[bold]TUTAJ[/bold]/nazwa-gry[/dim]\n")
        val = prompt("Place ID (lub Enter zeby pominac)")
        cfg["place_id"] = val
        save_cfg(cfg)

    while True:
        clear()
        header()

        status_line = (
            "[bold green]● DZIALA[/bold green]" if _running
            else "[dim]○ Zatrzymany[/dim]"
        )
        console.print(Panel(cfg_summary(cfg),
                            title=status_line,
                            border_style="bright_blue" if not _running else "green",
                            expand=False))
        console.print()

        if _running:
            console.print("  [bold white]1[/bold white]  Zarzadzaj (reczny rejoin / stop)")
        else:
            console.print("  [bold white]1[/bold white]  [bold green]START[/bold green]  — odpala Roblox i uruchamia tool")

        console.print("  [bold white]2[/bold white]  Ustawienia")
        console.print("  [bold white]3[/bold white]  Status Roblox")
        console.print("  [bold white]4[/bold white]  Reczny rejoin (jednorazowy)")
        console.print("  [bold white]0[/bold white]  Wyjscie")
        console.print()
        choice = prompt("Wybor")

        if choice == "1":
            if _running:
                screen_running(cfg)
            else:
                cfg = load_cfg()
                console.print()
                start_tool(cfg)
                if _running:
                    console.print("[bold green]✓ Tool uruchomiony![/bold green]")
                else:
                    console.print("[yellow]Ani timer ani watchdog nie sa wlaczone — sprawdz ustawienia.[/yellow]")
                pause()

        elif choice == "2":
            screen_settings(cfg)
            cfg = load_cfg()

        elif choice == "3":
            screen_status()

        elif choice == "4":
            console.print()
            do_rejoin(cfg, "Manual")
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
        console.print("\n[dim]Ctrl+C — wychodzę.[/dim]")
        sys.exit(0)
