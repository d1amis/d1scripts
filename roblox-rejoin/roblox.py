#!/usr/bin/env python3
# Roblox Rejoin Tool — multi-client (Lunex Delta, stock Roblox, etc.)
# Wymaga: pip install rich

import subprocess, threading, time, json, os, sys, re
from datetime import datetime
from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich         import box

console = Console()

CONFIG_PATH = os.path.join(os.environ.get("HOME", "."), ".rblx_rejoin.json")
DEFAULT_CFG = {
    "selected_pkg":  None,   # wybrany pakiet klienta
    "place_id":      "",
    "interval":      30,
    "watchdog":      True,
    "check_sec":     15,
    "launch_wait":   5,
}

# ─── Znane wzorce pakietów klientów Roblox ────────────────────────────────────
# pm list packages wypluje wszystkie, filtrujemy po tych fragmentach
CLIENT_PATTERNS = [
    "com.roblox",
    "lunex",
    "delta",
    "roblox",
    "bloxstrap",
    "solara",
    "arceus",
    "fluxus",
    "hydrogen",
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

# ─── wykrywanie klientow ──────────────────────────────────────────────────────

def get_installed_clients() -> list[dict]:
    """
    Zwraca liste slownikow {"pkg": ..., "label": ...}
    dla wszystkich zainstalowanych pakietow pasujacych do wzorcow.
    """
    rc, out, _ = subprocess.run(
        ["pm", "list", "packages"],
        capture_output=True, text=True
    ).returncode, \
    subprocess.run(["pm", "list", "packages"], capture_output=True, text=True).stdout, \
    None

    # kazda linia to "package:com.example.app"
    clients = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line.replace("package:", "").strip()
        pkg_lower = pkg.lower()
        if any(p in pkg_lower for p in CLIENT_PATTERNS):
            # ladna etykieta: ostatni segment pakietu
            label = pkg.split(".")[-1].replace("_", " ").title()
            # jesli to stock roblox
            if "com.roblox.client" == pkg:
                label = "Roblox (official)"
            clients.append({"pkg": pkg, "label": label})

    return clients

def get_app_label(pkg: str) -> str:
    """Probuje pobrac nazwe apki przez dumpsys."""
    rc, out, _ = subprocess.run(
        ["dumpsys", "package", pkg],
        capture_output=True, text=True
    ).returncode, \
    subprocess.run(["dumpsys", "package", pkg], capture_output=True, text=True).stdout, \
    None

    m = re.search(r"applicationInfo.*?label=([^\s]+)", out)
    if m:
        return m.group(1)
    return pkg.split(".")[-1]

# ─── android helpers ──────────────────────────────────────────────────────────

def _sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def is_client_running(pkg: str) -> bool:
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

def launch_client(pkg: str, place_id: str):
    if place_id:
        deep = f"roblox://experiences/start?placeId={place_id}"
        # probuj przez am start z intent VIEW skierowanym do konkretnego pakietu
        rc, _, _ = _sh([
            "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", deep,
            "-n", f"{pkg}/com.roblox.client.ActivityProtocol"  # stock activity
        ])
        if rc != 0:
            # fallback: bez konkretnej activity, tylko pakiet + deeplink
            rc, _, _ = _sh([
                "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", deep,
                "--package", pkg
            ])
        if rc != 0:
            # ostatni fallback: termux-open-url (system picker)
            _sh(["termux-open-url", deep])
    else:
        # bez place_id — odpal launcher pakietu
        _sh(["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"])

def do_rejoin(cfg, source=""):
    pkg = cfg.get("selected_pkg")
    if not pkg:
        console.print("[bold red]Brak wybranego klienta![/bold red]")
        return
    ts  = datetime.now().strftime("%H:%M:%S")
    tag = f"[dim][{source}][/dim] " if source else ""
    console.print(f"[dim]{ts}[/dim] {tag}[yellow]↓ Kill {pkg}...[/yellow]")
    kill_client(pkg)
    time.sleep(cfg["launch_wait"])
    pid = cfg["place_id"]
    console.print(f"[dim]{ts}[/dim] {tag}[green]↑ Launch {pkg}"
                  f"{' → place ' + pid if pid else ' (main menu)'}[/green]")
    launch_client(pkg, pid)

# ─── worker threads ───────────────────────────────────────────────────────────

def timer_worker(cfg):
    ivl = cfg["interval"] * 60
    while not _stop.wait(ivl):
        do_rejoin(cfg, "Timer")

def watchdog_worker(cfg):
    time.sleep(12)
    while not _stop.is_set():
        pkg = cfg.get("selected_pkg")
        if pkg and not is_client_running(pkg):
            console.print(f"\n[bold red][!][/bold red] [Watchdog] {pkg} zdechl — restart...")
            do_rejoin(cfg, "Watchdog")
            time.sleep(12)
        _stop.wait(cfg["check_sec"])

# ─── ui helpers ───────────────────────────────────────────────────────────────

def clear():
    os.system("clear")

def header():
    console.print(Panel(
        "[bold white]Roblox Rejoin Tool[/bold white]\n"
        "[dim]github.com/d1amis/d1scripts[/dim]",
        border_style="bright_blue",
        expand=False
    ))

def cfg_summary(cfg, clients):
    # znajdz label wybranego klienta
    pkg = cfg.get("selected_pkg")
    label = "[dim]nie wybrany[/dim]"
    if pkg:
        match = next((c for c in clients if c["pkg"] == pkg), None)
        label = f"[bold white]{match['label']}[/bold white]" if match else f"[yellow]{pkg}[/yellow]"

    tbl = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 2))
    tbl.add_row("[cyan]Klient[/cyan]",      label)
    tbl.add_row("[cyan]Place ID[/cyan]",
                f"[bold white]{cfg['place_id']}[/bold white]"
                if cfg["place_id"] else "[dim]nie ustawiony (main menu)[/dim]")
    tbl.add_row("[cyan]Interval[/cyan]",
                f"[bold white]{cfg['interval']} min[/bold white]")
    tbl.add_row("[cyan]Watchdog[/cyan]",
                "[bold green]ON[/bold green]" if cfg["watchdog"] else "[bold red]OFF[/bold red]")
    tbl.add_row("[cyan]Cooldown[/cyan]",    f"[white]{cfg['launch_wait']}s[/white]")
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
    if v in ("t", "y", "tak", "yes", "1"):  return True
    if v in ("n", "nie", "no", "0"):         return False
    return current

def pause():
    console.print("\n[dim]Nacisnij Enter...[/dim]", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass

# ─── ekran wyboru klienta ─────────────────────────────────────────────────────

def screen_pick_client(cfg) -> bool:
    """
    Skanuje zainstalowane paczki, pokazuje liste, pozwala wybrac.
    Zwraca True jesli wybrano, False jesli anulowano.
    """
    clear()
    header()
    console.print("[dim]Skanuje zainstalowane klienty Roblox...[/dim]\n")

    clients = get_installed_clients()

    if not clients:
        console.print(Panel(
            "[bold red]Nie znaleziono zadnego klienta Roblox![/bold red]\n"
            "[dim]Upewnij sie ze Roblox / Lunex Delta / inny klient jest zainstalowany.[/dim]",
            border_style="red", expand=False
        ))
        pause()
        return False

    tbl = Table(title="Wykryte klienty", box=box.ROUNDED, border_style="cyan")
    tbl.add_column("#",       style="bold yellow", width=4)
    tbl.add_column("Nazwa",   style="bold white")
    tbl.add_column("Pakiet",  style="dim")
    tbl.add_column("Status",  style="green")

    for i, c in enumerate(clients, 1):
        running = is_client_running(c["pkg"])
        status  = "[green]dziala[/green]" if running else "[dim]zatrzymany[/dim]"
        selected = " [cyan]←[/cyan]" if c["pkg"] == cfg.get("selected_pkg") else ""
        tbl.add_row(str(i), c["label"] + selected, c["pkg"], status)

    console.print(tbl)
    console.print()

    choice = prompt(f"Wybierz klienta (1-{len(clients)}) lub 0 aby anulowac")
    try:
        idx = int(choice)
    except ValueError:
        return False

    if idx == 0:
        return False
    if 1 <= idx <= len(clients):
        cfg["selected_pkg"] = clients[idx - 1]["pkg"]
        save_cfg(cfg)
        console.print(f"\n[bold green]✓ Wybrano:[/bold green] {clients[idx-1]['label']}")
        pause()
        return True

    return False

# ─── ustawienia ───────────────────────────────────────────────────────────────

def screen_settings(cfg, clients):
    while True:
        clear()
        header()
        console.print(Panel(cfg_summary(cfg, clients),
                            title="[bold]Ustawienia[/bold]",
                            border_style="cyan", expand=False))
        console.print()
        console.print("  [bold white]1[/bold white]  Wybierz klienta Roblox")
        console.print("  [bold white]2[/bold white]  Ustaw Place ID gry")
        console.print("  [bold white]3[/bold white]  Ustaw interval restartu (minuty)")
        console.print("  [bold white]4[/bold white]  Wlacz / wylacz Watchdog")
        console.print("  [bold white]5[/bold white]  Ustaw cooldown po killu (sekundy)")
        console.print("  [bold white]0[/bold white]  Powrot")
        console.print()
        choice = prompt("Wybor")

        if choice == "1":
            screen_pick_client(cfg)

        elif choice == "2":
            console.print()
            console.print("[dim]Place ID znajdziesz w URL gry na roblox.com:[/dim]")
            console.print("[dim]roblox.com/games/[bold]TUTAJ[/bold]/nazwa-gry[/dim]\n")
            val = prompt("Place ID", cfg["place_id"] or "")
            cfg["place_id"] = val
            save_cfg(cfg)
            console.print("[green]✓ Zapisano.[/green]")
            pause()

        elif choice == "3":
            console.print()
            val = prompt("Ile minut miedzy restartami", cfg["interval"])
            try:
                cfg["interval"] = int(val)
                save_cfg(cfg)
                console.print("[green]✓ Zapisano.[/green]")
            except ValueError:
                console.print("[red]Zla wartosc.[/red]")
            pause()

        elif choice == "4":
            console.print()
            cfg["watchdog"] = ask_yn("Watchdog aktywny?", cfg["watchdog"])
            save_cfg(cfg)
            console.print("[green]✓ Zapisano.[/green]")
            pause()

        elif choice == "5":
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

# ─── running screen ───────────────────────────────────────────────────────────

def screen_running(cfg, clients):
    global _running
    clear()
    header()

    pkg   = cfg.get("selected_pkg", "?")
    match = next((c for c in clients if c["pkg"] == pkg), None)
    label = match["label"] if match else pkg

    lines = [
        f"  Klient   : [bold white]{label}[/bold white]",
        f"  Place ID : [bold white]{cfg['place_id'] if cfg['place_id'] else 'main menu'}[/bold white]",
        f"  Interval : [bold white]{cfg['interval']} min[/bold white]",
        f"  Watchdog : {'[bold green]ON[/bold green]' if cfg['watchdog'] else '[bold red]OFF[/bold red]'}",
    ]
    console.print(Panel("\n".join(lines),
                        title="[bold green]● DZIALA[/bold green]",
                        border_style="green", expand=False))
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

# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    global _running

    cfg     = load_cfg()
    clients = get_installed_clients()

    # pierwsze uruchomienie lub brak wybranego klienta
    if not cfg.get("selected_pkg"):
        clear()
        header()
        if not clients:
            console.print(Panel(
                "[bold red]Nie znaleziono klientow Roblox![/bold red]\n"
                "Zainstaluj Roblox lub Lunex Delta i uruchom ponownie.",
                border_style="red", expand=False
            ))
            sys.exit(1)

        console.print(Panel(
            "[bold]Pierwsze uruchomienie![/bold]\n"
            "Wykryto kilka klientow Roblox — wybierz ktory uzyc.",
            border_style="yellow", expand=False
        ))
        console.print()
        screen_pick_client(cfg)
        cfg = load_cfg()

        # zapytaj o place id od razu
        if not cfg["place_id"]:
            clear()
            header()
            console.print("[dim]roblox.com/games/[bold]TUTAJ[/bold]/nazwa-gry[/dim]\n")
            val = prompt("Place ID (lub Enter zeby pominac)")
            cfg["place_id"] = val
            save_cfg(cfg)

    while True:
        # odswiez liste klientow przy kazdym powrocie do menu
        clients = get_installed_clients()
        clear()
        header()

        status_line = "[bold green]● DZIALA[/bold green]" if _running else "[dim]○ Zatrzymany[/dim]"
        console.print(Panel(cfg_summary(cfg, clients),
                            title=status_line,
                            border_style="green" if _running else "bright_blue",
                            expand=False))
        console.print()

        if _running:
            console.print("  [bold white]1[/bold white]  Zarzadzaj (reczny rejoin / stop)")
        else:
            console.print("  [bold white]1[/bold white]  [bold green]START[/bold green]  — odpala klienta i uruchamia tool")

        console.print("  [bold white]2[/bold white]  Ustawienia")
        console.print("  [bold white]3[/bold white]  Pokaz wykryte klienty")
        console.print("  [bold white]4[/bold white]  Reczny rejoin (jednorazowy)")
        console.print("  [bold white]0[/bold white]  Wyjscie")
        console.print()
        choice = prompt("Wybor")

        if choice == "1":
            if _running:
                screen_running(cfg, clients)
            else:
                if not cfg.get("selected_pkg"):
                    console.print("[red]Najpierw wybierz klienta w Ustawieniach![/red]")
                    pause()
                    continue
                cfg = load_cfg()
                console.print()
                start_tool(cfg)
                if _running:
                    console.print("[bold green]✓ Tool uruchomiony![/bold green]")
                else:
                    console.print("[yellow]Ani timer ani watchdog nie sa wlaczone.[/yellow]")
                pause()

        elif choice == "2":
            screen_settings(cfg, clients)
            cfg = load_cfg()

        elif choice == "3":
            clear()
            header()
            if not clients:
                console.print("[red]Brak wykrytych klientow.[/red]")
            else:
                tbl = Table(title="Wykryte klienty", box=box.ROUNDED, border_style="cyan")
                tbl.add_column("#",      style="bold yellow", width=4)
                tbl.add_column("Nazwa",  style="bold white")
                tbl.add_column("Pakiet", style="dim")
                tbl.add_column("Status", style="green")
                for i, c in enumerate(clients, 1):
                    running = is_client_running(c["pkg"])
                    status  = "[green]dziala[/green]" if running else "[dim]zatrzymany[/dim]"
                    sel     = " [cyan]← wybrany[/cyan]" if c["pkg"] == cfg.get("selected_pkg") else ""
                    tbl.add_row(str(i), c["label"] + sel, c["pkg"], status)
                console.print(tbl)
            pause()

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
