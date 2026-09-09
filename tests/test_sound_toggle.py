import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import json
import pytest
from engine.notifications import is_sound_enabled, set_sound_enabled, play_surebet_sound, SOUND_CONFIG_FILE

def test_sound_toggle():
    # Zapis stanu początkowego
    initial = is_sound_enabled()
    
    try:
        # 1. Wyłączenie dźwięku
        assert set_sound_enabled(False) is True
        assert is_sound_enabled() is False
        
        # Weryfikacja zapisu w pliku JSON
        assert os.path.exists(SOUND_CONFIG_FILE)
        with open(SOUND_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            assert data.get('sound_enabled') is False
            
        # Wywołanie odtwarzania przy wyłączonym dźwięku nie rzuca błędów i wychodzi natychmiast
        play_surebet_sound()
        
        # 2. Włączenie dźwięku
        assert set_sound_enabled(True) is True
        assert is_sound_enabled() is True
        with open(SOUND_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            assert data.get('sound_enabled') is True
            
    finally:
        # Przywrócenie stanu początkowego
        set_sound_enabled(initial)


def test_default_sound_is_false():
    # Przy braku pliku konfiguracyjnego domyślny stan to False
    bak_path = SOUND_CONFIG_FILE + ".bak"
    try:
        if os.path.exists(SOUND_CONFIG_FILE):
            os.replace(SOUND_CONFIG_FILE, bak_path)
        assert is_sound_enabled() is False
    finally:
        if os.path.exists(bak_path):
            os.replace(bak_path, SOUND_CONFIG_FILE)
