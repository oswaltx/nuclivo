# SPDX-License-Identifier: GPL-3.0-only
"""English UI translations. German remains the source language."""
import os

_language = 'de'

EN = {
    'Abbrechen': 'Cancel', 'Aktionen': 'Actions', 'Aktualisieren': 'Refresh',
    'Aktualisiere …': 'Refreshing…', 'Aktualisieren fehlgeschlagen: ': 'Refresh failed: ',
    'Alben': 'Albums', 'Album ist leer': 'Album is empty', 'Album löschen': 'Delete album',
    'Album löschen?': 'Delete album?', 'Album gelöscht': 'Album deleted',
    'Album erstellt': 'Album created', 'Album umbenennen': 'Rename album',
    'Alle Fotos offline verfügbar': 'Make all photos available offline',
    'Alle Freigaben beenden': 'Stop all sharing', 'Alle Elemente im Papierkorb werden endgültig gelöscht.': 'All items in the trash will be deleted permanently.',
    'Alle gespeicherten Vorschaubilder werden gelöscht und bei Bedarf neu geladen.': 'All saved thumbnails will be removed and downloaded again when needed.',
    'Auch alle bisherigen Fotos herunterladen': 'Also download existing photos',
    'Berechtigung': 'Permission', 'Bearbeiten': 'Edit', 'Befehl kopieren': 'Copy command',
    'Bestehenden Ordner wählen …': 'Choose existing folder…', 'Besitzer': 'Owner',
    'Bilder und Videos': 'Images and videos', 'Dateien hochladen': 'Upload files',
    'Dateien hochladen …': 'Upload files…', 'Damit Monate sofort erscheinen. Braucht nur wenig Platz, lädt aber einmalig jedes Foto. Gedrosselt auf ~50 % der Leitung, nur solange Nuclivo offen ist.': 'Shows months immediately. Uses little space but downloads each photo once. Limited to about half your bandwidth while Nuclivo is open.',
    'Der Datei-Sync nutzt rclone und braucht eine eigene Anmeldung.': 'File sync uses rclone and requires a separate login.',
    'Der Papierkorb ist leer': 'Trash is empty', 'Der Sync-Dienst läuft gerade nicht.': 'The sync service is not running.',
    'Details stehen unten im Protokoll.': 'Details are shown in the log below.',
    'Diese Ordner werden in beide Richtungen abgeglichen.': 'These folders are synchronized in both directions.',
    'Die Dateien bleiben lokal und in Proton Drive erhalten.': 'Files remain on your computer and in Proton Drive.',
    'Die Fotos bleiben in deiner Foto-Mediathek erhalten.': 'The photos remain in your photo library.',
    'Diesen Ordner verwenden': 'Use this folder', 'Download fehlgeschlagen: ': 'Download failed: ',
    'E-Mail-Adresse': 'Email address', 'Einladen': 'Invite', 'Einrichtung abgeschlossen': 'Setup complete',
    'Eigenschaften': 'Properties', 'Element(e), ': 'item(s), ',
    'Endgültig löschen': 'Delete permanently', 'Endgültig löschen?': 'Delete permanently?',
    'Entfernen': 'Remove', 'Erledigte entfernen': 'Clear completed', 'Erneut versuchen': 'Try again',
    'Erstellen': 'Create', 'Erstellt': 'Created', 'Fehler': 'Error', 'Fehler: ': 'Error: ',
    'Fehlgeschlagen': 'Failed', 'Fertig': 'Done', 'Fortsetzen': 'Resume',
    'Foto konnte nicht geladen werden': 'Could not load photo', 'Foto wird geladen …': 'Loading photo…',
    'Foto-Sync aus – bereits gespeicherte Fotos bleiben erhalten': 'Photo sync off — downloaded photos remain',
    'Fotos': 'Photos', 'Fotos hochladen': 'Upload photos', 'Fotos hochgeladen': 'Photos uploaded',
    'Fotos offline verfügbar': 'Photos available offline', 'Fotos offline verfügbar machen': 'Make photos available offline',
    'Fotos synchronisieren': 'Sync photos', 'Fotos werden offline gespeichert: ': 'Saving photos offline: ',
    'Fotos werden offline gespeichert (gedrosselt)': 'Saving photos offline (rate limited)',
    'Freigabe verlassen': 'Leave share', 'Freigabe verlassen?': 'Leave share?',
    'Freigaben beendet': 'Sharing stopped', 'Füge unten einen Ordner hinzu oder schalte den Foto-Sync ein.': 'Add a folder below or enable photo sync.',
    'Gebaut mit Claude': 'Built with Claude', 'Geändert': 'Modified', 'Geräte': 'Devices',
    'Geteilt': 'Shared', 'Größe': 'Size', 'Hauptmenü': 'Main menu',
    'Herunterladen': 'Download', 'Herunterladen nach …': 'Download to…',
    'Hierher ': 'Move or copy here: ', 'Hochgeladen': 'Uploaded', 'Heute, ': 'Today, ',
    'Im Browser öffnen': 'Open in browser', 'In eigenem Fenster öffnen': 'Open in separate window',
    'In Proton Drive (Web) öffnen': 'Open Proton Drive on the web',
    'In den Papierkorb': 'Move to trash', 'In diesem Ordner suchen': 'Search this folder',
    'Jetzt synchronisieren': 'Sync now', 'Jeder mit dem Link kann zugreifen.': 'Anyone with the link can access this.',
    'Keine Alben': 'No albums', 'Keine Fotos': 'No photos', 'Keine Treffer': 'No results',
    'Keine Unterordner': 'No subfolders', 'Keine Übertragungen': 'No transfers',
    'Konto': 'Account', 'Kopieren': 'Copy', 'Kopieren nach …': 'Copy to…',
    'Link aktualisieren': 'Update link', 'Link erstellen': 'Create link', 'Link kopiert': 'Link copied',
    'Link löschen': 'Delete link', 'Link gelöscht': 'Link deleted', 'Link gespeichert': 'Link saved',
    'Link-Passwort': 'Link password', 'Läuft ab am (JJJJ-MM-TT, optional)': 'Expires on (YYYY-MM-DD, optional)',
    'Lädt die ganze Mediathek, gedrosselt auf ~50 % der Leitung': 'Downloads the whole library at about half your bandwidth',
    'Läuft …': 'Running…', 'Leeren': 'Empty', 'Live-Sync läuft, mit Fehlern': 'Live sync is running with errors',
    'Lokale Änderungen werden sofort übertragen': 'Local changes are transferred immediately',
    'Lokale Änderungen werden sofort übertragen, Proton wird alle ': 'Local changes are transferred immediately. Proton is checked every ',
    'Lokaler Ordner': 'Local folder', 'Lokalen Ordner wählen': 'Choose local folder',
    'Los geht’s': 'Get started', 'Melde dich im Browser bei Proton an.': 'Sign in to Proton in your browser.',
    'Mit mir geteilt': 'Shared with me', 'Meine Dateien': 'My files',
    'Mit + einen Ordner hinzufügen': 'Use + to add a folder',
    'Nachricht (optional)': 'Message (optional)', 'Neue Fotos aus dem Ordner hochladen': 'Upload new photos from folder',
    'Neue Fotos aus Proton herunterladen': 'Download new photos from Proton',
    'Neue Tabelle': 'New spreadsheet', 'Neues Album': 'New album', 'Neues Dokument': 'New document',
    'Neuer Ordner': 'New folder', 'Neuer Ordner …': 'New folder…',
    'Neu laden': 'Reload', 'Neu verbinden': 'Reconnect', 'Neu: Meine Dateien/': 'New: My files/',
    'Nicht angemeldet': 'Not signed in', 'Nicht verbunden': 'Not connected',
    'Nicht mehr synchronisieren': 'Stop syncing', 'Nicht mehr synchronisieren?': 'Stop syncing?',
    'Noch keine Einträge.': 'No entries yet.', 'Noch keine Ordner': 'No folders yet',
    'Noch nicht eingerichtet': 'Not set up yet', 'Oberfläche für die offizielle Proton Drive CLI. Kein offizielles Proton-Produkt.': 'Interface for the official Proton Drive CLI. Not an official Proton product.',
    'Ohne Datum · ': 'No date · ', 'Optionen': 'Options', 'Ordner': 'Folder',
    'Ordner hinzufügen': 'Add folder', 'Ordner hochladen': 'Upload folder',
    'Ordner hochladen …': 'Upload folder…', 'Ordner für Fotos': 'Photo folder',
    'Ordner konnte nicht ermittelt werden': 'Could not identify folder', 'Ordner öffnen': 'Open folder',
    'Papierkorb': 'Trash', 'Papierkorb leeren': 'Empty trash', 'Papierkorb leeren?': 'Empty trash?',
    'Papierkorb geleert': 'Trash emptied', 'Pausieren': 'Pause', 'Pausiert': 'Paused',
    'Personen': 'People', 'Protokoll': 'Log', 'Rückgängig': 'Undo',
    'Sortieren': 'Sort', 'Sortiert nach Jahr/Monat': 'Sorted by year/month',
    'Starten': 'Start', 'Status unbekannt: ': 'Status unknown: ',
    'Suchen': 'Search', 'Symbolischer Link: Öffnen abgebrochen': 'Symbolic link: opening cancelled',
    'Sync-Anmeldung einrichten': 'Set up sync login', 'Synchronisation': 'Sync',
    'Synchronisation gestartet': 'Sync started', 'Synchronisiert gerade …': 'Syncing…',
    'Synchronisiert …': 'Syncing…', 'Teilen': 'Share', 'Teilen …': 'Share…',
    'Unbekannter Fehler': 'Unknown error', 'Unsicherer Dateiname: Öffnen abgebrochen': 'Unsafe filename: opening cancelled',
    'Upload fehlgeschlagen: ': 'Upload failed: ', 'Umbenannt': 'Renamed',
    'Umbenennen': 'Rename', 'Umbenennen …': 'Rename…', 'Verlassen': 'Leave',
    'Verwalten': 'Manage', 'Verbinden': 'Connect', 'Verbunden': 'Connected',
    'Von mir geteilt': 'Shared by me', 'Vorschau-Cache geleert': 'Thumbnail cache cleared',
    'Vorschau-Cache leeren': 'Clear thumbnail cache', 'Vorschau-Cache leeren?': 'Clear thumbnail cache?',
    'Vorschauen im Hintergrund laden': 'Load previews in background',
    'Vorschauen werden geladen': 'Loading previews', 'Vorschauen werden im Hintergrund geladen': 'Loading previews in background',
    'Vorschauen: ': 'Previews: ', 'Wartet auf ersten Abgleich': 'Waiting for first sync',
    'Wiederhergestellt': 'Restored', 'Wiederherstellen': 'Restore',
    'Wird gespeichert: ': 'Saving: ', 'Wird geladen …': 'Loading…',
    'Willkommen': 'Welcome', 'Willkommen bei Nuclivo': 'Welcome to Nuclivo',
    'Ziel in Proton Drive': 'Destination in Proton Drive', 'Zielordner wählen': 'Choose destination folder',
    'Zuletzt ': 'Last: ', 'Zuletzt geändert': 'Last modified',
    'Zurück zu den Dateien': 'Back to files', 'Zwei Fragen zu deinen Fotos. Beides kannst du später jederzeit oben rechts im Fotos-Menü ändern.': 'Two questions about your photos. You can change both later in the Photos menu.',
    'all Fotos': 'all photos', 'alle Fotos': 'all photos',
    ' übersprungen': ' skipped',
}


def set_language(language):
    global _language
    _language = language if language in ('de', 'en') else 'de'


def get_language():
    return _language


def tr(text):
    if _language != 'en':
        return text
    return EN.get(text, text)


def system_language():
    return 'de' if (os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG') or '').lower().startswith('de') else 'en'

EN.update({
    'Abmelden': 'Sign out', 'Abmelden?': 'Sign out?', 'Abgemeldet': 'Signed out',
    'Anmelden': 'Sign in', 'Angemeldet': 'Signed in',
    'Anmeldung im Browser wird geöffnet …': 'Opening sign-in in your browser…',
    'Anmeldung fehlgeschlagen: ': 'Sign-in failed: ',
    'Bitte Nuclivo neu starten, um die Sprache zu wechseln.': 'Restart Nuclivo to change the language.',
    'Sprache': 'Language', 'Über Nuclivo': 'About Nuclivo', 'Übertragungen': 'Transfers',
    'Öffentlicher Link': 'Public link',
    'Passwortgeschützte Links bitte in Proton Drive im Browser verwalten.': 'Manage password-protected links in Proton Drive on the web.',
    'Zwei Fragen zu deinen Fotos. Beides kannst du später jederzeit oben rechts im Fotos-Menü ändern.': 'Two questions about your photos. You can change both later in the Photos menu.',
    'Proton-Fotos mit einem lokalen Ordner abgleichen. Löschungen werden nie übertragen.': 'Sync Proton photos with a local folder. Deletions are never propagated.',
    'Änderungen in Proton prüfen alle … Minuten': 'Check Proton for changes every … minutes',
    'Änderungen werden gerade abgeglichen.': 'Changes are being synchronized.',
    'Ordner hinzugefügt – erster Abgleich startet': 'Folder added — initial sync is starting',
    'Die Proton Drive CLI wird auf diesem Gerät abgemeldet. Der Sync-Dienst funktioniert davon unabhängig weiter.': 'The Proton Drive CLI will be signed out on this device. The sync service uses a separate session.',
    'Datum': 'Date',
})

EN.update({
    'alle {count} Fotos': 'all {count} photos',
    'Speichert {count}{size} in {folder} und hält sie aktuell. Vorschauen entstehen dann direkt aus diesen Dateien.': 'Saves {count}{size} in {folder} and keeps them up to date. Previews are then made from these files.',
    'Führe im Terminal ~/.local/bin/rclone config aus. Erstelle einen Remote namens nuclivo-proton mit Typ protondrive. Gib deine Zugangsdaten ausschließlich an den interaktiven Eingabeaufforderungen ein. rclone speichert Passwörter wiederherstellbar in seiner Konfiguration; schütze diese Datei und teile sie niemals.': 'Run ~/.local/bin/rclone config in a terminal. Create a remote named nuclivo-proton of type protondrive. Enter your credentials only at the interactive prompts. rclone stores recoverable passwords in its configuration; protect that file and never share it.',
    'Übergeordneter Ordner': 'Parent folder', 'Hinzufügen': 'Add',
    'Ändern': 'Change', 'Öffnen': 'Open', 'Löschen': 'Delete',
    'Gelöscht': 'Deleted',
})

EN.update({
    'Januar': 'January', 'Februar': 'February', 'März': 'March', 'April': 'April',
    'Mai': 'May', 'Juni': 'June', 'Juli': 'July', 'August': 'August',
    'September': 'September', 'Oktober': 'October', 'November': 'November', 'Dezember': 'December',
    'Heute, {time}': 'Today, {time}', 'Gestern, {time}': 'Yesterday, {time}',
    'Fehler: {error}': 'Error: {error}',
    'Aktualisieren fehlgeschlagen: {error}': 'Refresh failed: {error}',
    'Status unbekannt: {error}': 'Status unknown: {error}',
    'Zuletzt {time}': 'Last: {time}',
    '{count} Ordner': '{count} folders', '{count} Dateien': '{count} files',
    '{count} Fotos': '{count} photos', 'Ohne Datum · {count}': 'No date · {count}',
    'Vorschauen: {done} von {total}': 'Previews: {done} of {total}',
    'Fotos werden offline gespeichert: {done} von {total}': 'Saving photos offline: {done} of {total}',
    '{count} Element(e), {size}': '{count} item(s), {size}',
    ', {count} übersprungen': ', {count} skipped',
    'Alles synchron': 'Everything synced',
    'Lokale Änderungen werden sofort übertragen, Proton wird alle {minutes} Minuten geprüft.': 'Local changes are transferred immediately. Proton is checked every {minutes} minutes.',
    'Ziel für „{name}“ in Drive': 'Destination for “{name}” in Drive',
    'Wohin soll „{name}“ synchronisiert werden?': 'Where should “{name}” be synced?',
    'Neu: Meine Dateien/{name}': 'New: My files/{name}',
    '„{name}“ wird geöffnet …': 'Opening “{name}”…',
    '„{name}“ heruntergeladen': 'Downloaded “{name}”',
    'Download fehlgeschlagen: {error}': 'Download failed: {error}',
    'Upload fehlgeschlagen: {error}': 'Upload failed: {error}',
    'Ordner „{name}“ erstellt': 'Created folder “{name}”',
    '„{name}“ im Papierkorb': '“{name}” moved to trash',
    '„{name}“ lässt sich danach nicht wiederherstellen.': '“{name}” cannot be restored afterward.',
    'Du verlierst den Zugriff auf „{name}“.': 'You will lose access to “{name}”.',
    'Anmeldung fehlgeschlagen: {error}': 'Sign-in failed: {error}',
    'Verschieben': 'Move',
})

EN.update({
    'Dieser Ordner ist leer': 'This folder is empty',
    'Zieh Dateien hierher, um sie hochzuladen.': 'Drop files here to upload them.',
    'Zum Hochladen loslassen': 'Release to upload',
    'Name': 'Name', 'Sonst nur Fotos ab dem Einschalten': 'Otherwise only photos added after enabling sync',
    'Abfrage': 'Polling', 'Verschieben nach …': 'Move to…',
    'Dieser Ordner wird schon synchronisiert': 'This folder is already being synced',
})

EN.update({
    'pausiert': 'paused',
    'wartet, sichtbare Fotos zuerst': 'waiting; visible photos first',
    'gedrosselt auf ~50 % der Leitung': 'limited to ~50% of bandwidth',
    ' · gedrosselt auf ~50 %': ' · limited to ~50%',
    'Wird gespeichert: {done} von {total} · gedrosselt': 'Saving: {done} of {total} · rate limited',
})

EN.update({
    '{email} entfernt': '{email} removed',
    '{email} eingeladen': '{email} invited',
    'Alben: {error}': 'Albums: {error}',
    '{count} Foto(s)': '{count} photo(s)',
    '„{name}“ {action}': '“{name}” {action}',
    'verschoben': 'moved', 'kopiert': 'copied',
    '{action} nach': '{action} to', 'Hierher {action}': '{action} here',
})

EN.update({
    'Konfiguration geladen: %d Ordnerpaar(e), Fotos %s': 'Configuration loaded: %d folder pair(s), photos %s',
    'an': 'on', 'aus': 'off',
    'Erster Abgleich (resync): %s <-> %s': 'Initial sync (resync): %s <-> %s',
    '✓ %s synchronisiert (%.0fs)': '✓ %s synced (%.0fs)',
    'Bisync-Zustand verloren: manuellen Wiederherstellungsabgleich durchführen': 'Bisync state lost: manual recovery required',
    'Foto-Sync gestartet: %d vorhandene Fotos werden übersprungen': 'Photo sync started: skipping %d existing photos',
    '%d Foto(s) werden heruntergeladen': 'Downloading %d photo(s)',
    'Foto-Download fehlgeschlagen: %s': 'Photo download failed: %s',
    '%d lokale Foto(s) werden hochgeladen': 'Uploading %d local photo(s)',
    'Foto-Upload fehlgeschlagen: %s': 'Photo upload failed: %s',
    '✓ Fotos synchronisiert': '✓ Photos synced',
    'Fehler bei %s': 'Error for %s',
    'Überwache %d Ordner auf Änderungen': 'Watching %d folders for changes',
    'Manueller Sync angefordert': 'Manual sync requested',
})
