"""Self-update for DUO (v0.55, standing order 2026-11-17).

On launch DUO silently checks GitHub for a newer Release (the
v0.28 check in duo_ollama). When one is out, a popup asks
whether to update; only on the user's tap does DUO pull it --
and the pull goes through the verified path: the release's
checksum sidecar is downloaded first, the zip's SHA-256 must
match it, and nothing is installed from an unchecked download.
Once pulled, a second popup asks whether to restart now to
complete the update: Yes applies the update and restarts DUO on
its own; No leaves the verified update staged, and it is applied
the next time DUO starts.

The staging area is `.duo-update/` inside the app folder (the
folder holding dual_chat.py -- the install folder, wherever the
user put it). It holds the downloaded zip, the checksum sidecar,
the version, and a `ready` marker written last: only a staging
with the marker counts as pending, so an interrupted download
can never install a half-written update. Applying extracts the
zip over the app folder (the zip's `duo/` prefix is stripped,
file modes are restored from the zip), so the user's own files
in the folder -- saved pairs, the debug log -- are untouched:
only files the release ships are overwritten. Files a release
*removes* are not cleaned up: a stale module from an older
version stays on disk (a known limitation; no release has
removed a file yet).

This module is stdlib-only at the top level (it imports cleanly
without PySide6, like duo_workers): the two popups and the
restart import Qt lazily inside their functions.
"""

import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile

from duo_constants import MSG_UPDATE_DOWNLOADING, MSG_UPDATE_PENDING
from duo_ollama import parse_version, update_check_url
from duo_workers import _UpdateDownloader


UPDATE_DIR_NAME = ".duo-update"
UPDATE_ZIP_NAME = "update.zip"
UPDATE_SHA_NAME = "update.zip.sha256"
UPDATE_VERSION_NAME = "version.txt"
UPDATE_READY_NAME = "ready"
UPDATE_TMP_SUFFIX = ".tmp"
UPDATE_ZIP_PREFIX = "duo/"
DOWNLOAD_TIMEOUT = 10
DOWNLOAD_CHUNK = 65536


class UpdateError(Exception):
    """An update step failed, with a plain-language reason for the
    user. Raised by the helpers; the two public entry points
    (download_and_stage, apply_pending_update) catch it and
    return ("failed", reason) instead, so the GUI layer never
    sees an exception."""


class _Cancelled(Exception):
    """The download was cancelled (the window closed mid-download).
    Not an error: the caller reports it as cancelled, not failed."""


def app_root(root=None):
    """The app folder: the folder holding this module (it sits next
    to dual_chat.py), or `root` when one is given (tests)."""
    if root is not None:
        return root
    return os.path.dirname(os.path.abspath(__file__))


def staging_dir(root=None):
    """The staging area inside the app folder."""
    return os.path.join(app_root(root), UPDATE_DIR_NAME)


def fetch_release_assets(url=None, urlopen=None):
    """Ask for the latest DUO release and pick out its download
    file and checksum file. Returns (version, zip_url, sha_url,
    zip_bytes) where version is the normalized dotted number from
    the tag and zip_bytes is the asset's size in bytes (or None
    when the release doesn't say). Raises UpdateError when the
    release can't be read, the tag doesn't parse, or either file
    is missing -- a release without a checksum file is refused:
    DUO never installs an update it can't check."""
    if url is None:
        url = update_check_url()
    if urlopen is None:
        urlopen = urllib.request.urlopen
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "DUO-update-check"})
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError("GitHub said no (HTTP %s)." % exc.code)
    except Exception:
        raise UpdateError(
            "Couldn't reach GitHub to find the update. "
            "Are you online?")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("GitHub's answer didn't make sense.")
    if not isinstance(payload, dict):
        raise UpdateError("GitHub's answer didn't make sense.")
    version = parse_version(payload.get("tag_name"))
    if version is None:
        raise UpdateError("GitHub's answer didn't make sense.")
    dotted = ".".join(str(n) for n in version)
    zip_name = "duo-%s.zip" % dotted
    sha_name = zip_name + ".sha256"
    zip_url = sha_url = None
    zip_bytes = None
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("name") == zip_name:
            zip_url = asset.get("browser_download_url")
            size = asset.get("size")
            zip_bytes = size if isinstance(size, int) else None
        elif asset.get("name") == sha_name:
            sha_url = asset.get("browser_download_url")
    if not zip_url:
        raise UpdateError("The release has no DUO download file.")
    if not sha_url:
        raise UpdateError(
            "The release has no checksum file, so the download "
            "can't be checked. DUO won't install an update it "
            "can't check.")
    return (dotted, zip_url, sha_url, zip_bytes)


def parse_sha256_sidecar(text):
    """The hex digest from a .sha256 sidecar: either a bare hex
    string or sha256sum's "<hex>  <filename>" line. Returns None
    when the text holds no 64-hex-digit digest."""
    if not isinstance(text, str):
        return None
    for token in text.split():
        token = token.strip().lower()
        if (len(token) == 64
                and all(c in "0123456789abcdef" for c in token)):
            return token
    return None


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(DOWNLOAD_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _download(url, dest, urlopen, progress=None, total=None,
              cancel=None):
    """Download `url` to `dest` in chunks. progress(downloaded,
    total) is called after each chunk when given (total is -1
    when the size is unknown); cancel is a threading.Event
    checked between chunks. A cancelled or failed download
    removes the partial file. Raises _Cancelled on cancel,
    UpdateError on failure."""
    downloaded = 0
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "DUO-update-check"})
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            with open(dest, "wb") as fh:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise _Cancelled()
                    try:
                        chunk = response.read(DOWNLOAD_CHUNK)
                    except Exception:
                        raise UpdateError(
                            "The download broke off. Are you still "
                            "online?")
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded,
                                 total if total is not None else -1)
    except (_Cancelled, UpdateError):
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    except Exception:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise UpdateError(
            "Couldn't download the update. Are you online?")


def download_and_stage(root=None, url=None, urlopen=None,
                       progress=None, cancel=None):
    """Pull the latest release: download its checksum file, then
    its zip, check the zip's SHA-256 against the checksum, and
    stage the verified update for the restart. Returns ("staged",
    version) on success, ("cancelled", None) when the download
    was cancelled, ("failed", reason) otherwise. Never raises.
    Any staging from an earlier download is cleared first, so the
    staging always holds the newest verified download."""
    if urlopen is None:
        urlopen = urllib.request.urlopen
    staging = staging_dir(root)
    try:
        discard_staged_update(root)
        version, zip_url, sha_url, zip_bytes = fetch_release_assets(
            url=url, urlopen=urlopen)
        try:
            request = urllib.request.Request(
                sha_url, headers={"User-Agent": "DUO-update-check"})
            with urlopen(request,
                         timeout=DOWNLOAD_TIMEOUT) as response:
                sidecar = response.read().decode("utf-8")
        except Exception:
            raise UpdateError(
                "Couldn't download the checksum file. "
                "Are you online?")
        expected = parse_sha256_sidecar(sidecar)
        if expected is None:
            raise UpdateError(
                "The checksum file didn't make sense, so the "
                "download can't be checked.")
        os.makedirs(staging, exist_ok=True)
        tmp_zip = os.path.join(
            staging, UPDATE_ZIP_NAME + UPDATE_TMP_SUFFIX)
        _download(zip_url, tmp_zip, urlopen, progress=progress,
                  total=zip_bytes, cancel=cancel)
        if _sha256_file(tmp_zip) != expected:
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass
            return ("failed",
                    "The download didn't match its checksum, so "
                    "it wasn't installed. Try again, or download "
                    "it from GitHub yourself.")
        os.replace(tmp_zip, os.path.join(staging, UPDATE_ZIP_NAME))
        with open(os.path.join(staging, UPDATE_SHA_NAME), "w",
                  encoding="utf-8") as fh:
            fh.write(sidecar)
        with open(os.path.join(staging, UPDATE_VERSION_NAME), "w",
                  encoding="utf-8") as fh:
            fh.write(version)
        # The marker goes last: it is the commit point. A staging
        # without it (an interrupted download) never counts as
        # pending, so a half-written update can never install.
        with open(os.path.join(staging, UPDATE_READY_NAME), "w",
                  encoding="utf-8") as fh:
            fh.write(version)
        return ("staged", version)
    except _Cancelled:
        return ("cancelled", None)
    except UpdateError as exc:
        return ("failed", str(exc))
    except Exception:
        return ("failed",
                "The download failed. Try again, or download it "
                "from GitHub yourself.")


def has_pending_update(root=None):
    """True when a verified update is staged and waiting: the
    `ready` marker, the zip, the checksum and the version are
    all there. A staging without the marker (an interrupted
    download) doesn't count."""
    staging = staging_dir(root)
    for name in (UPDATE_READY_NAME, UPDATE_ZIP_NAME, UPDATE_SHA_NAME,
                 UPDATE_VERSION_NAME):
        if not os.path.isfile(os.path.join(staging, name)):
            return False
    return True


def _apply_zip(zip_path, dest_root):
    """Extract `zip_path` over `dest_root`: the zip's `duo/`
    prefix is stripped, file modes are restored from the zip,
    and anything outside the prefix -- or any path that would
    escape the folder -- refuses the whole update instead of
    installing it. Raises UpdateError."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise UpdateError("The saved update isn't a valid file.")
    with archive:
        members = archive.infolist()
        if not members:
            raise UpdateError("The saved update is empty.")
        for info in members:
            name = info.filename
            if (os.path.isabs(name) or ".." in name.split("/")
                    or not name.startswith(UPDATE_ZIP_PREFIX)):
                raise UpdateError(
                    "The saved update holds files DUO doesn't "
                    "recognize, so it wasn't installed.")
        for info in members:
            rel = info.filename[len(UPDATE_ZIP_PREFIX):]
            if not rel:
                continue  # The duo/ folder entry itself.
            target = os.path.join(dest_root, *rel.split("/"))
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as fh:
                shutil.copyfileobj(src, fh)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass


def apply_pending_update(root=None):
    """Install a staged update: re-check the staged zip against
    its saved checksum, extract it over the app folder, and clear
    the staging. Returns ("applied", version), ("none", None)
    when nothing is staged, or ("failed", reason). A failed apply
    clears the staging, so a broken update is never retried on
    every launch -- the next update check offers it again. Never
    raises."""
    staging = staging_dir(root)
    if not has_pending_update(root):
        return ("none", None)
    try:
        with open(os.path.join(staging, UPDATE_VERSION_NAME),
                  encoding="utf-8") as fh:
            version = fh.read().strip()
        with open(os.path.join(staging, UPDATE_SHA_NAME),
                  encoding="utf-8") as fh:
            expected = parse_sha256_sidecar(fh.read())
        zip_path = os.path.join(staging, UPDATE_ZIP_NAME)
        if expected is None or _sha256_file(zip_path) != expected:
            discard_staged_update(root)
            return ("failed",
                    "The saved update didn't check out, so it "
                    "wasn't installed. Check for updates to try "
                    "again.")
        _apply_zip(zip_path, app_root(root))
        discard_staged_update(root)
        return ("applied", version)
    except UpdateError as exc:
        discard_staged_update(root)
        return ("failed", str(exc))
    except Exception:
        discard_staged_update(root)
        return ("failed",
                "The update couldn't be installed. Check for "
                "updates to try again, or download it from "
                "GitHub yourself.")


def discard_staged_update(root=None):
    """Remove the staging area. Never raises."""
    shutil.rmtree(staging_dir(root), ignore_errors=True)


def restart_argv():
    """The command that restarts this process: the same Python
    (the venv's, when DUO runs installed) and the same entry
    script with the same arguments, as an absolute path so the
    restart works whatever the working directory is."""
    entry = os.path.abspath(sys.argv[0])
    return [sys.executable, entry] + list(sys.argv[1:])


def restart_now():
    """Replace this process with a fresh copy of DUO (the same
    command that started it). Never returns on success; raises
    OSError when the restart can't start."""
    argv = restart_argv()
    os.execv(argv[0], argv)


def restart_quietly():
    """restart_now(), but a restart that can't start just returns
    False instead of raising: the app keeps running the old code
    and the next launch loads the new files. True is only ever
    seen by tests that stub os.execv (a real restart never
    returns)."""
    try:
        restart_now()
    except OSError:
        return False
    return True


def _qt_widgets():
    from PySide6 import QtWidgets
    return QtWidgets


def offer_update(parent, version, state=None):
    """The update popup: "DUO %s is out. Update now?" Returns True
    when the user taps Update, False on Not now (or closing the
    popup). One popup at a time: `state` (the window's state
    dict, when given) carries the `update_offer_open` flag, and
    no popup opens over another dialog -- the caller keeps the
    status note as the reminder either way. "Not now" is the
    default button, so a stray Enter key never starts a download
    the user didn't ask for."""
    QtWidgets = _qt_widgets()
    if state is not None and state.get("update_offer_open"):
        return False
    if QtWidgets.QApplication.activeModalWidget() is not None:
        return False
    if state is not None:
        state["update_offer_open"] = True
    try:
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("DUO %s is out" % version)
        box.setText("DUO %s is available. Update now?" % version)
        box.setInformativeText(
            "The download is checked against the release "
            "checksum before anything is installed.")
        update_btn = box.addButton(
            "Update", QtWidgets.QMessageBox.AcceptRole)
        later_btn = box.addButton(
            "Not now", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(later_btn)
        box.exec()
        return box.clickedButton() is update_btn
    finally:
        if state is not None:
            state["update_offer_open"] = False


def ask_restart(parent, version):
    """The restart popup, after the update is pulled and checked:
    "Restart now to complete the update?" Returns True on Restart
    now, False on Later (the update finishes on the next start).
    "Later" is the default button, so a stray Enter key never
    restarts DUO out from under the user."""
    QtWidgets = _qt_widgets()
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("Update ready")
    box.setText("DUO %s is downloaded and checked. Restart now "
                "to complete the update?" % version)
    box.setInformativeText(
        "Later waits until the next time DUO starts.")
    restart_btn = box.addButton(
        "Restart now", QtWidgets.QMessageBox.AcceptRole)
    later_btn = box.addButton(
        "Later", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(later_btn)
    box.exec()
    return box.clickedButton() is restart_btn


def apply_and_restart(window, state, root=None):
    """The Restart-now path: install the staged update, then close
    the window and quit so main() restarts DUO on the new files.
    Returns ("applied", version) on success, ("failed", reason)
    when the install failed (the window stays open and the reason
    is for the status bar)."""
    applied, payload = apply_pending_update(root)
    if applied == "none":
        # The staging vanished between the download and the tap
        # (deleted by hand, or another copy of DUO installed it).
        return ("failed",
                "The saved update is gone. Check for updates to "
                "try again.")
    if applied != "applied":
        return (applied, payload)
    state["restart_for_update"] = True
    window.close()
    QtWidgets = _qt_widgets()
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.quit()
    return ("applied", payload)


def install_update_wiring(window, alive, download_fn=None):
    """Wire the download half of the update flow into a built
    window: the download worker, its progress and result slots,
    and the test hooks. Called from build_window after the
    window's state dict exists (the wiring reads signals, state
    and the status writer from it). Returns the downloader list
    (the window's close handler waits for it). download_fn is
    the pull to run -- dual_chat passes its own download_and_stage
    name, so tests stub the network by patching
    dual_chat.download_and_stage (the v0.28 seam)."""
    dual = window._dual_chat
    signals = dual["signals"]
    state = dual["state"]
    set_status_message = dual["set_status_message"]
    logger = dual["debug_logger"]
    downloaders = []
    dual["update_downloaders"] = downloaders

    def run_update_download(version):
        # One download at a time: a second Update tap while a
        # download runs just re-states the note.
        if any(d.thread.isRunning() for d in downloaders):
            set_status_message(MSG_UPDATE_DOWNLOADING % version)
            return
        downloader = _UpdateDownloader(
            signals, download_fn=download_fn)
        downloaders.append(downloader)
        downloader.thread.finished.connect(
            lambda: downloaders.remove(downloader)
            if downloader in downloaders else None)
        set_status_message(MSG_UPDATE_DOWNLOADING % version)
        downloader.thread.start()

    def on_update_progress(downloaded, total):
        if not alive():
            return
        if total and total > 0:
            note = "Downloading update... %d KB of %d KB" % (
                downloaded // 1024, total // 1024)
        else:
            note = "Downloading update... %d KB" % (
                downloaded // 1024)
        set_status_message(note)

    def on_update_downloaded(result):
        if not alive():
            return
        status, payload = result
        if status == "staged":
            logger.action("update staged: DUO %s" % payload)
            if ask_restart(window, payload):
                done, reason = apply_and_restart(window, state)
                if done != "applied":
                    set_status_message(reason)
            else:
                set_status_message(MSG_UPDATE_PENDING)
        elif status == "failed":
            logger.info("update download failed")
            set_status_message(payload)
        else:  # Cancelled: the window is closing anyway.
            logger.info("update download cancelled")

    signals.update_downloaded.connect(on_update_downloaded)
    signals.update_progress.connect(on_update_progress)
    dual["run_update_download"] = run_update_download
    dual["on_update_downloaded"] = on_update_downloaded
    dual["on_update_progress"] = on_update_progress
    return downloaders
