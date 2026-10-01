# -*- coding: utf-8 -*-
"""Player lektora Dune Lektor PL: odtwarza nagrania kwestii na komendę z moda Lua (UE4SS).

Komunikacja: mod dopisuje linie do pliku komend (cmd.txt), player go śledzi.
    PLAY <id>   -> odtwórz nagranie kwestii (honoruje aliasy; <id>_f bez nagrania -> wersja męska <id>);
                   gdy lektor jeszcze mówi: zostało <= SKIP_GRACE s -> nowa czeka (1, najnowsza),
                   zostało więcej -> gracz pominął dialog, bieżąca jest przerywana
    STOP        -> przerwij i wyczyść kolejkę
    GLOSNOSC 0-100 -> suwak z menu (skala kwadratowa, slider_gain); VOL x -> wzmocnienie wprost (1.0 = nagranie)
    SPEED x / GRACE s -> tempo, próg pominięcia (menu Mod Settings)

Dwa tryby:
  * wydanie (exe z PyInstallera, --audio <katalog>): nagrania <id>.opus z wypalonym tempem (audio.json),
    aliasy w <katalog>/aliases.csv, bez ffmpeg i scipy;
  * deweloperski (python player.py): WAV-y z voice/out_*/chunks, aliasy z voice/plans, tempo przez ffmpeg.
Argumenty: [mod.ini] [--audio DIR] [--cmd PLIK] [--log PLIK] [--test = bez wykrywania gry] [--child]
Player kończy pracę sam, gdy gra zostanie zamknięta (<= 2 s), albo gdy gry nie ma od startu (20 s).
"""
import csv, glob, json, os, subprocess, sys, time

FROZEN = getattr(sys, "frozen", False)
SELF = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))

# Start z moda (os.execute/start) dziedziczy otwarte uchwyty gry i launchera — np. WPS State\active-mods.txt zostawał
# zablokowany, dopóki player żył. Dlatego pierwsza kopia tylko uruchamia czystą (close_fds = bez dziedziczenia) i kończy się.
if __name__ == "__main__" and "--child" not in sys.argv:
    subprocess.Popen(SELF + sys.argv[1:] + ["--child"], close_fds=True,
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    sys.exit(0)

import numpy as np
import sounddevice as sd
import soundfile as sf


def arg(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else default


POSITIONAL = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and not sys.argv[i - 1].startswith("--")]
INI_PATH = POSITIONAL[0] if POSITIONAL else ""
RELEASE_AUDIO = arg("--audio")
CMD = arg("--cmd") or os.path.join(HERE, "cmd.txt")
LOG = arg("--log") or os.path.join(HERE, "player.log")

OUTPUT = ""       # fragment nazwy urządzenia (np. "HyperX", mod.ini: Urzadzenie=); pusty = domyślne urządzenie Windows
SPEED = 1.15      # tempo lektora bez zmiany wysokości głosu
VOLUME = 0.70     # głośność (1.0 = nagranie)
SKIP_GRACE = 2.0  # nowa kwestia, gdy lektorowi zostało <= tyle s -> czeka i leci po niej; więcej -> pominięcie, przerwij
GAP = 0.15        # przerwa między kolejnymi kwestiami z kolejki [s]
HANG_S = 5.0      # sd.stop/sd.play wisi dłużej (HDMI audio znika przy zmianie trybu ekranu) -> restart playera
NO_GAME_EXIT = 20
GAME_EXE_RE = r'"dunesandbox[^"]*shipping[^"]*\.exe"'  # Steam: DuneSandbox-Win64-Shipping.exe; MS Store: inny wariant

# --- źródła nagrań ---------------------------------------------------------------------------------------------
if RELEASE_AUDIO:
    AUDIO_DIRS, PATTERN = [RELEASE_AUDIO], "{}.opus"
    try:
        BAKED = float(json.load(open(os.path.join(RELEASE_AUDIO, "audio.json"), encoding="utf-8"))["tempo"])
    except Exception:
        BAKED = 1.0
    ALIAS_FILES = [os.path.join(RELEASE_AUDIO, "aliases.csv")]
else:
    V = r"F:\DuneLektor\voice"
    AUDIO_DIRS = [os.path.join(V, d, "chunks") for d in (
        # Głos B (Mat 2): referencja mat_voice_v2.wav, preset lektor, exaggeration 0.3; stare katalogi jako zapas.
        "out_start_M_v2", "out_cine_start_M_v2", "out_biom1_M_v2", "out_reszta_M_v2",
        "out_female_v2",  # wersje żeńskie (<id>_f), ten sam lektor
        "out_start_M", "out_cine_start_M", "out_biom1_M", "probe_mat")]
    PATTERN, BAKED = "{}_01.wav", 1.0
    ALIAS_FILES = glob.glob(os.path.join(V, "plans", "aliases_*.csv"))

ALIASES = {}
for p in ALIAS_FILES:
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            ALIASES[r["row"]] = r["same_as_row"]


def find_audio(row):
    for r in ([row, row[:-2]] if row.endswith("_f") else [row]):  # wersja żeńska bez nagrania -> męska
        r = ALIASES.get(r, r)
        for d in AUDIO_DIRS:
            p = os.path.join(d, PATTERN.format(r))
            if os.path.exists(p):
                return p
    return None


# --- przetwarzanie dźwięku -------------------------------------------------------------------------------------
def resample(data, sr, target):
    if sr == target:
        return data
    try:
        import soxr
        return soxr.resample(data, sr, target).astype("float32")
    except ImportError:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(target), int(sr))
        return resample_poly(data, target // g, sr // g, axis=0).astype("float32")


def stretch(data, factor):
    """Tempo bez zmiany wysokości głosu (WSOLA, audiotsm) — w wydaniu tylko korekta względem wypalonego tempa."""
    from audiotsm import wsola
    from audiotsm.io.array import ArrayReader, ArrayWriter
    ch = data.shape[1]
    reader, writer = ArrayReader(np.ascontiguousarray(data.T)), ArrayWriter(ch)
    wsola(ch, speed=factor).run(reader, writer)
    return np.ascontiguousarray(writer.data.T).astype("float32")


def ffmpeg_tempo(path, factor):
    """Tryb deweloperski: przyspieszona kopia przez ffmpeg atempo, trzymana w cache_tempo (brzmi jak dotąd)."""
    import hashlib
    cache = os.path.join(HERE, "cache_tempo")
    os.makedirs(cache, exist_ok=True)
    key = hashlib.md5(f"{os.path.abspath(path)}|{os.path.getmtime(path)}".encode()).hexdigest()[:8]
    out = os.path.join(cache, f"{os.path.splitext(os.path.basename(path))[0]}_{key}_x{int(round(factor * 100))}.wav")
    if not os.path.exists(out):
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-af", f"atempo={factor}", out],
                           check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except FileNotFoundError:
            return None
    return out if os.path.exists(out) else None


def load(path):
    factor = SPEED / BAKED
    if abs(factor - 1.0) >= 0.01 and not RELEASE_AUDIO:
        fast = ffmpeg_tempo(path, factor)
        if fast:
            path, factor = fast, 1.0
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if abs(factor - 1.0) >= 0.01:
        data = stretch(data, factor)
    return data * VOLUME, sr


# --- wyjście audio ---------------------------------------------------------------------------------------------
def pick_device():
    """Domyślne urządzenie wyjściowe (albo OUTPUT), ale przez WASAPI (nie stare MME — to ono psuło brzmienie)."""
    devices = list(enumerate(sd.query_devices()))
    wasapi = [(i, d) for i, d in devices
              if d["max_output_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"]
    if OUTPUT:
        for i, d in wasapi:
            if OUTPUT.lower() in d["name"].lower():
                return i
    default_name = sd.query_devices(kind="output")["name"]
    for i, d in wasapi:
        if d["name"][:20] == default_name[:20]:
            return i
    return None


def reinit_audio():
    """Odświeża listę urządzeń PortAudio (jest zapamiętywana przy starcie) i wybiera urządzenie od nowa."""
    global DEVICE, DEVICE_SR
    try:
        sd._terminate(); sd._initialize()
    except Exception as e:
        print("BLAD reinicjalizacji audio:", e)
    DEVICE = pick_device()
    dev = sd.query_devices(DEVICE) if DEVICE is not None else sd.query_devices(kind="output")
    DEVICE_SR = int(dev["default_samplerate"])
    print(f"{time.strftime('%H:%M:%S')} audio: '{dev['name']}' ({sd.query_hostapis(dev['hostapi'])['name']}, {DEVICE_SR} Hz)")


PLAY_END = 0.0       # kiedy skończy się bieżąca kwestia (time.time())
PLAYING_SINCE = 0.0  # >0 = trwa wywołanie sd.stop/sd.play
CURRENT = None       # id bieżącej kwestii


def busy():
    return time.time() < PLAY_END


def play(path):
    global PLAY_END, PLAYING_SINCE
    data, sr = load(path)
    for attempt in range(2):
        out = resample(data, sr, DEVICE_SR)   # WASAPI (tryb współdzielony) wymaga częstotliwości urządzenia
        PLAY_END = time.time() + len(out) / DEVICE_SR + GAP
        PLAYING_SINCE = time.time()
        try:
            sd.stop()
            sd.play(out, DEVICE_SR, device=DEVICE)
            PLAYING_SINCE = 0.0
            return
        except Exception as e:
            PLAYING_SINCE = 0.0
            PLAY_END = 0.0
            print("BLAD odtwarzania:", e)
            reinit_audio()   # urządzenie zniknęło/zmieniło się -> nowa lista urządzeń, druga próba


def hang_guard():
    """Osobny wątek: jeśli sd.stop/sd.play (w wątku głównym — WASAPI tego wymaga) wisi > HANG_S, restart playera."""
    while True:
        time.sleep(1)
        if PLAYING_SINCE and time.time() - PLAYING_SINCE > HANG_S:
            print(f"{time.strftime('%H:%M:%S')} odtwarzanie zawieszone > {HANG_S}s (urządzenie audio?) — restart playera")
            subprocess.Popen(SELF + sys.argv[1:], close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
            os._exit(3)


def start_next(queue):
    """Gdy lektor milczy, odtwarza pierwszą czekającą kwestię."""
    global CURRENT
    if queue and not busy():
        CURRENT, path = queue.pop(0)
        play(path)
        print(f"{time.strftime('%H:%M:%S')} PLAY {CURRENT} -> {os.path.basename(path)}")


# --- proces ----------------------------------------------------------------------------------------------------
def single_instance():
    """Drugi player = podwójny głos. Mutex systemowy blokuje uruchomienie kolejnej kopii."""
    import ctypes
    for _ in range(20):  # po restarcie poprzednia kopia może jeszcze chwilę trzymać mutex
        h = ctypes.windll.kernel32.CreateMutexW(None, True, "Local\\DuneLektorPlayer")
        if ctypes.windll.kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return h
        ctypes.windll.kernel32.CloseHandle(h)
        time.sleep(0.5)
    print("Player już działa — ta kopia się zamyka.")
    sys.exit(2)


def game_running():
    # /FO CSV: pełna nazwa procesu (w zwykłym widoku tasklist ucina ją do 25 znaków: "DuneSandbox-Win64-Shippin")
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout.lower()
    import re
    return re.search(GAME_EXE_RE, out) is not None


def slider_gain(v):
    """Suwak Głośność 0-100 -> wzmocnienie. Kwadrat: ucho słyszy logarytmicznie, liniowy suwak zmieniał za mało
    (70 -> 40 to tylko ~-5 dB, gracz: „gada tak samo głośno”). 100 -> 1.0, 85 -> 0.72, 40 -> 0.16."""
    v = max(0.0, min(100.0, v))
    return (v / 100.0) ** 2


def load_ini(path):
    """Ustawienia z mod.ini moda (menu Mod Settings): Glosnosc 0-100, Tempo, Pomijanie [s], Urzadzenie (tekst)."""
    global VOLUME, SPEED, SKIP_GRACE, OUTPUT
    import configparser
    if not path or not os.path.exists(path):
        return
    c = configparser.ConfigParser(comment_prefixes=(";", "#"), inline_comment_prefixes=None, interpolation=None)
    c.read(path, encoding="utf-8")
    sec = c["DuneLektorPL"] if c.has_section("DuneLektorPL") else {}
    try:
        if "Glosnosc" in sec: VOLUME = slider_gain(float(sec["Glosnosc"]))
        if "Tempo" in sec: SPEED = max(0.5, min(2.0, float(sec["Tempo"])))
        if "Pomijanie" in sec: SKIP_GRACE = max(0.0, min(30.0, float(sec["Pomijanie"])))
        if "Urzadzenie" in sec: OUTPUT = sec["Urzadzenie"].strip()
    except ValueError as e:
        print("BLAD mod.ini:", e)
    print(f"ustawienia z {path}: glosnosc {VOLUME:.2f}, tempo {SPEED}, pomijanie {SKIP_GRACE}s"
          + (f", urządzenie '{OUTPUT}'" if OUTPUT else ""))


def main():
    global PLAY_END, SPEED, VOLUME, SKIP_GRACE
    if sys.stdout is None:        # bez konsoli (pythonw / exe okienkowe) -> log do pliku
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
    single_instance()
    load_ini(INI_PATH)
    # Duży bufor + wysoki priorytet: gra zajmuje CPU, a mały bufor WASAPI daje trzaski/"robota".
    sd.default.latency = "high"
    sd.default.blocksize = 4096
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000)  # ABOVE_NORMAL
    except Exception:
        pass
    reinit_audio()
    import threading
    threading.Thread(target=hang_guard, daemon=True).start()
    open(CMD, "a").close()
    pos = os.path.getsize(CMD)
    print(f"DuneLektor player ({'wydanie' if RELEASE_AUDIO else 'dev'}): nagrania {AUDIO_DIRS[0]}, komendy {CMD}")
    seen_game, next_check, started = False, 0.0, time.time()
    queue = []          # [(id, ścieżka)] — czeka najwyżej 1, najnowsza
    while True:
        if time.time() >= next_check and "--test" not in sys.argv:  # koniec gry = koniec playera (--test: bez gry)
            next_check = time.time() + 2   # crash/zamknięcie gry -> player znika po <= 2 s
            if game_running():
                if not seen_game:
                    print(f"{time.strftime('%H:%M:%S')} gra wykryta")
                seen_game = True
            elif seen_game:
                print(f"{time.strftime('%H:%M:%S')} gra zamknięta — player kończy pracę")
                return
            elif time.time() - started > NO_GAME_EXIT:
                print(f"{time.strftime('%H:%M:%S')} gry brak od {NO_GAME_EXIT}s — player kończy pracę")
                return
        size = os.path.getsize(CMD)
        if size < pos:            # plik wyczyszczony
            pos = 0
        if size > pos:
            with open(CMD, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                parts = line.strip().split(" ", 1)
                if not parts or not parts[0]:
                    continue
                try:
                    val = float(parts[1].replace(",", ".")) if len(parts) > 1 and parts[0] in ("SPEED", "VOL", "GRACE", "GLOSNOSC") else None
                except ValueError:
                    continue
                if parts[0] == "SPEED" and val is not None:
                    SPEED = max(0.5, min(2.0, val)); print(f"SPEED {SPEED}")
                elif parts[0] == "GLOSNOSC" and val is not None:   # pozycja suwaka z Mod Settings
                    VOLUME = slider_gain(val); print(f"GLOSNOSC {val:g} -> VOL {VOLUME:.2f}")
                elif parts[0] == "VOL" and val is not None:
                    VOLUME = max(0.0, min(2.0, val)); print(f"VOL {VOLUME}")
                elif parts[0] == "GRACE" and val is not None:
                    SKIP_GRACE = max(0.0, min(30.0, val)); print(f"GRACE {SKIP_GRACE}")
                elif parts[0] == "STOP":
                    sd.stop(); queue.clear(); PLAY_END = 0.0
                    print("STOP")
                elif parts[0] == "PLAY" and len(parts) > 1:
                    row = parts[1].strip()
                    path = find_audio(row)
                    if not path:
                        print(f"BRAK NAGRANIA dla {row}")
                    elif (busy() and row == CURRENT) or any(r == row for r, _ in queue):
                        pass    # ta sama kwestia już gra / czeka (gra wysyła ją ponownie)
                    else:
                        if queue:
                            print(f"POMIJAM czekającą {queue[0][0]}")
                        queue[:] = [(row, path)]
                        remaining = PLAY_END - time.time()
                        if busy() and remaining > SKIP_GRACE:
                            # Lektorowi zostało dużo -> gracz pominął dialog: przerwij i czytaj nową od razu.
                            print(f"PRZERYWAM {CURRENT} (zostało {remaining:.1f}s) -> {row}")
                            PLAY_END = 0.0
                        elif busy():
                            print(f"CZEKA {row} (zostało {remaining:.1f}s)")
                        start_next(queue)
        start_next(queue)
        time.sleep(0.03)


if __name__ == "__main__":
    main()
