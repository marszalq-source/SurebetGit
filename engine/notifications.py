"""
Moduł powiadomień dla SurebetGit
Obsluguje dzwięk oszczędnościowy (winsound) oraz Toast Notifications w Windows (winotify).
"""
import sys
import threading

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
                toast.set_audio(audio.Default, loop=False)
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
