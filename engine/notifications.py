"""
Moduł powiadomień dla SurebetGit
Obsluguje dzwięk oszczędnościowy (winsound) oraz Toast Notifications w Windows (winotify).
"""
import os
import json
import sys
import threading

CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUND_CONFIG_FILE = os.path.join(CONFIG_DIR, "sound_config.json")


def is_sound_enabled() -> bool:
    """Zwraca True jeśli dźwięk jest włączony, False jeśli wyciszony (domyślnie: False)."""
    try:
        if os.path.exists(SOUND_CONFIG_FILE):
            with open(SOUND_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("sound_enabled", False))
    except Exception:
        pass
    return False


def set_sound_enabled(enabled: bool) -> bool:
    """Zapisuje stan włączenia dźwięków do pliku sound_config.json."""
    try:
        with open(SOUND_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"sound_enabled": bool(enabled)}, f, indent=2)
        return True
    except Exception as e:
        print(f"[SoundConfig Error] {e}")
        return False

try:
    import winsound
except ImportError:
    winsound = None

try:
    from winotify import Notification, audio
except ImportError:
    Notification = None


def play_surebet_sound():
    """Odtwarza w osobnym wątku chwytliwy sygnał powiadomienia o surebecie (zysk!)."""
    if not is_sound_enabled():
        return

    def _beep():
        if winsound:
            try:
                # 3 melodyjne tony: C, E, G (Do-Mi-Sol)
                winsound.Beep(523, 150)  # C5
                winsound.Beep(659, 150)  # E5
                winsound.Beep(784, 250)  # G5
            except Exception:
                pass
    threading.Thread(target=_beep, daemon=True).start()


def send_windows_notification(title: str, msg: str, icon_path: str = ""):
    """Wysyła natywne powiadomienie Toast w Windows 10/11."""
    def _notify():
        if Notification:
            try:
                toast = Notification(
                    app_id="SurebetGit",
                    title=title,
                    msg=msg,
                    duration="short"
                )
                if is_sound_enabled():
                    toast.set_audio(audio.Default, loop=False)
                else:
                    toast.set_audio(audio.Silent, loop=False)
                toast.show()
            except Exception as e:
                print(f"[Notification Error] {e}")
    threading.Thread(target=_notify, daemon=True).start()


def notify_surebet_found(match_name: str, profit_percent: float, bookies: str, sound_enabled: bool = True, notifications_enabled: bool = True):
    if sound_enabled:
        play_surebet_sound()
    if notifications_enabled:
        send_windows_notification(
            title=f"🔥 Okazja Surebet +{profit_percent}%!",
            msg=f"Mecz: {match_name}\nBukmacherzy: {bookies}"
        )
