"""Translations: gettext catalogs from usr/share/locale, in the user's chosen language or the system's, else English."""

import gettext
import json
import os
from pathlib import Path

# kiro-gdrive has no other preferences; the language choice lives next to its private rclone.conf
PREFS_PATH = os.path.expanduser("~/.config/kiro-gdrive/prefs.json")

DOMAIN = "kiro-gdrive"
# usr/share/<app>/ -> usr/share/locale: /usr/share/locale once installed, the repo's own copy when run from git
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"

# Each language in its own name, so someone stuck in a language they can't read still finds theirs.
NAMES = {
    "en": "English", "nl": "Nederlands", "de": "Deutsch", "es": "Español", "fr": "Français",
    "pt_BR": "Português (Brasil)", "ru": "Русский", "hu": "Magyar", "it": "Italiano", "tr": "Türkçe",
    "pl": "Polski", "ro": "Română", "zh_CN": "简体中文", "ja": "日本語",
}


def available():
    """Return the language codes the app can show: English, then every language with a catalog."""
    return ["en", *sorted(p.parent.parent.name for p in LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.mo"))]


def name(code):
    """Return the language's own name for code (nl_BE -> Nederlands), or the code itself."""
    return NAMES.get(code) or NAMES.get(code.split("_")[0]) or code


def _load_prefs():
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return {}
    return prefs if isinstance(prefs, dict) else {}


def chosen():
    """Return the language saved in the app, or None to follow the system."""
    code = _load_prefs().get("language")
    return code if code in available() else None


def save_choice(code):
    """Save the app's language; None follows the system again. Shows after the next start."""
    prefs = _load_prefs()
    prefs["language"] = code
    os.makedirs(os.path.dirname(PREFS_PATH), exist_ok=True)
    with open(PREFS_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)


def _system_language():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").split(":")[0].split(".")[0]
        if value:
            return value
    return "en"


# A variant without its own catalog borrows its sibling's: Portugal reads Brazilian Portuguese, Taiwan and Hong Kong
# read simplified Chinese, rather than falling back to English.
_SIBLING = {"pt": "pt_BR", "zh": "zh_CN"}


def _shown(code):
    # the language the app really shows for a system code: its catalog (nl_BE -> nl), its sibling, else English
    codes = available()
    base = code.split("_")[0]
    for candidate in (code, base, _SIBLING.get(base)):
        if candidate in codes:
            return candidate
    return "en"


SYSTEM = _shown(_system_language())
# KGD_LANGUAGE (the live test pins English with it) beats the saved choice, which beats the system language
ACTIVE = os.environ.get("KGD_LANGUAGE") or chosen()
_SYSTEM_LANGUAGE_VAR = os.environ.get("LANGUAGE")
if ACTIVE:
    # GTK's own dialogs and the app names from Gio read LANGUAGE too, so they follow the choice
    os.environ["LANGUAGE"] = ACTIVE


def restore_environment():
    """Put LANGUAGE back as the session set it, so a restarted app sees the real system language again."""
    if _SYSTEM_LANGUAGE_VAR is None:
        os.environ.pop("LANGUAGE", None)
    else:
        os.environ["LANGUAGE"] = _SYSTEM_LANGUAGE_VAR

# the catalog _shown() picked, so the sibling rule applies; gettext's own lookup would stop at English for pt_PT
_translation = gettext.translation(DOMAIN, localedir=LOCALE_DIR, languages=[ACTIVE or SYSTEM], fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext


def N_(text):
    """Mark text for translation without translating it (a lookup key that is translated where it is shown)."""
    return text
