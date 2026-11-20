"""Saved model pairs + relay presets: the dropdown handlers.

v0.56 (P1-12): extracted from dual_chat.py's build_window() so the
window file stays under its 4,000-line bound. The handlers used to
be nested closures over the pair row, the preset row, and the relay
row widgets; they now live on a small controller constructed with
exactly the widgets and callbacks they touch, and build_window()
wires the controller's methods to the buttons. Nothing here is
loaded by default: the dropdowns only list what is saved, and
loading a pair or preset only happens on a tap.

The storage itself lives in duo_storage (pairs_path, presets_path,
load/save/delete); this module is the UI half: fill the dropdowns,
save the row as it stands, load a saved setup back onto the row,
delete with a confirmation first.
"""

# No PySide6 import at the top: this module must import cleanly
# without it installed, so the first-run guard in main() can print
# its plain message instead of a traceback (v0.14). Qt comes in
# lazily, inside the methods that use it, like every other duo_*
# module.
from duo_constants import MSG_PAIR_NEEDS_NAME, MSG_PRESET_NEEDS_NAME
from duo_storage import (
    delete_pair, delete_preset, load_pairs, load_presets,
    pair_tooltip, pairs_file_corrupt, pairs_path, preset_tooltip,
    presets_file_corrupt, presets_path, save_pair, save_preset,
)
from duo_text import parse_stop_phrases


class PairPresetController:
    """The pair + preset dropdowns and their buttons.

    Constructed once per window with the widgets the handlers
    touch. `set_status_message` and `refresh_buttons` are the
    window's own callbacks, passed in so the handlers can report
    and re-enable exactly as they did as nested closures.
    """

    def __init__(self, window, convo, widgets, set_status_message,
                 refresh_buttons, pair_combo, pair_name, pair_save,
                 pair_delete, preset_combo, preset_name, preset_save,
                 preset_delete, start_picker, mode_picker, cap_spin,
                 stop_edit, agree_check, review_check):
        self.window = window
        self.convo = convo
        self.widgets = widgets
        self.set_status_message = set_status_message
        self.refresh_buttons = refresh_buttons
        self.pair_combo = pair_combo
        self.pair_name = pair_name
        self.pair_save = pair_save
        self.pair_delete = pair_delete
        self.preset_combo = preset_combo
        self.preset_name = preset_name
        self.preset_save = preset_save
        self.preset_delete = preset_delete
        self.start_picker = start_picker
        self.mode_picker = mode_picker
        self.cap_spin = cap_spin
        self.stop_edit = stop_edit
        self.agree_check = agree_check
        self.review_check = review_check

    def wire(self):
        """Connect the buttons and dropdowns to the handlers."""
        self.pair_save.clicked.connect(self.on_pair_save)
        self.pair_delete.clicked.connect(self.on_pair_delete)
        # Both signals: currentIndexChanged covers picking a
        # different pair, activated covers re-picking the pair that
        # is already selected (choosing the same item again fires no
        # index change, but the user still expects the load).
        # Loading twice is harmless -- it applies the same setup.
        self.pair_combo.currentIndexChanged.connect(
            self.on_pair_selected)
        self.pair_combo.activated.connect(self.on_pair_selected)
        self.preset_save.clicked.connect(self.on_preset_save)
        self.preset_delete.clicked.connect(self.on_preset_delete)
        # Both signals, as with pairs: currentIndexChanged covers
        # picking a different preset, activated covers re-picking
        # the preset that is already selected.
        self.preset_combo.currentIndexChanged.connect(
            self.on_preset_selected)
        self.preset_combo.activated.connect(self.on_preset_selected)

    # --- saved model pairs -------------------------------------------

    def refresh_pair_combo(self, select=None):
        """Reload the pair dropdown from the JSON file. Nothing is
        loaded by default -- the dropdown just lists what is saved.
        A file that exists but won't parse earns a plain note, the
        same as the presets row (v0.60, P1-16)."""
        from PySide6 import QtCore
        pairs = load_pairs(pairs_path())
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        names = sorted(pairs)
        for index, name in enumerate(names):
            self.pair_combo.addItem(name)
            # Hovering shows what loading this pair would set (v0.41,
            # R6): models and the instructions preview.
            self.pair_combo.setItemData(
                index, pair_tooltip(pairs[name]),
                QtCore.Qt.ToolTipRole)
        if select in names:
            self.pair_combo.setCurrentIndex(names.index(select))
        else:
            self.pair_combo.setCurrentIndex(-1)
        self.pair_combo.blockSignals(False)
        if pairs_file_corrupt(pairs_path()):
            self.set_status_message(
                "Couldn't read the saved pairs.")
        # The Delete button follows the selection, and this rebuild
        # runs with signals blocked, so refresh the buttons here.
        self.refresh_buttons()
        return pairs

    def on_pair_save(self):
        name = self.pair_name.text().strip()
        if not name:
            self.set_status_message(MSG_PAIR_NEEDS_NAME)
            return
        setup = {
            "model_a": self.convo.sides["A"].model,
            "model_b": self.convo.sides["B"].model,
            "instruction_a":
                self.widgets["A"]["instr_edit"].toPlainText(),
            "instruction_b":
                self.widgets["B"]["instr_edit"].toPlainText(),
        }
        try:
            save_pair(pairs_path(), name, setup)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            self.set_status_message(
                "Couldn't save pair '%s': %s" % (name, reason))
            return
        self.refresh_pair_combo(select=name)
        self.set_status_message("Pair '%s' saved." % name)

    def on_pair_selected(self, index):
        if index < 0:
            return
        name = self.pair_combo.currentText()
        setup = load_pairs(pairs_path()).get(name)
        if setup is None:
            return
        missing = []
        for side, key in (("A", "model_a"), ("B", "model_b")):
            model = setup.get(key)
            if not model:
                continue
            picker = self.widgets[side]["picker"]
            # The plain model name is the item data (v0.33); the
            # visible text carries size and fit too.
            found = picker.findData(model)
            if found >= 0:
                picker.setCurrentIndex(found)
            else:
                missing.append(model)
        # Instructions follow the pair too (v0.41, R6). A key
        # missing from an old pair leaves the current value alone --
        # only keys the pair actually has are applied. (Pairs saved
        # before v0.59 also hold temperatures; the FYRN engine has
        # no temperature knob, so those keys are simply ignored.)
        for side, ikey in (
                ("A", "instruction_a"),
                ("B", "instruction_b")):
            if isinstance(setup.get(ikey), str):
                self.widgets[side]["instr_edit"].setPlainText(
                    setup[ikey])
        if missing:
            self.set_status_message(
                "Pair '%s' loaded, but not installed: %s."
                % (name, ", ".join(missing)))
        else:
            self.set_status_message("Pair '%s' loaded." % name)
        self.refresh_buttons()

    def on_pair_delete(self):
        index = self.pair_combo.currentIndex()
        if index < 0:
            return
        name = self.pair_combo.currentText()
        from PySide6 import QtWidgets
        answer = QtWidgets.QMessageBox.question(
            self.window, "Delete pair?",
            "Delete the saved pair '%s'? This can't be undone." % name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            delete_pair(pairs_path(), name)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            self.set_status_message(
                "Couldn't delete pair '%s': %s" % (name, reason))
            return
        self.refresh_pair_combo()
        self.set_status_message("Pair '%s' deleted." % name)

    # --- relay presets -------------------------------------------------
    # Same shape as the saved pairs above: reload the dropdown from
    # the JSON file, save the row as it stands, load a preset back
    # onto the row, delete with a confirmation first.

    def refresh_preset_combo(self, select=None):
        """Reload the preset dropdown from the JSON file. Nothing is
        loaded by default -- the dropdown just lists what is saved.
        A file that exists but won't parse earns a plain note; the
        row itself is left exactly as it is."""
        from PySide6 import QtCore
        presets = load_presets(presets_path())
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        names = sorted(presets)
        for index, name in enumerate(names):
            self.preset_combo.addItem(name)
            # Hovering shows the six relay fields this preset sets.
            self.preset_combo.setItemData(
                index, preset_tooltip(presets[name]),
                QtCore.Qt.ToolTipRole)
        if select in names:
            self.preset_combo.setCurrentIndex(names.index(select))
        else:
            self.preset_combo.setCurrentIndex(-1)
        self.preset_combo.blockSignals(False)
        if presets_file_corrupt(presets_path()):
            self.set_status_message(
                "Couldn't read the saved relay presets.")
        # The Delete button follows the selection, and this rebuild
        # runs with signals blocked, so refresh the buttons here.
        self.refresh_buttons()
        return presets

    def on_preset_save(self):
        name = self.preset_name.text().strip()
        if not name:
            self.set_status_message(MSG_PRESET_NEEDS_NAME)
            return
        config = {
            "start_side": self.start_picker.currentText(),
            "mode": ("aware"
                     if self.mode_picker.currentText() == "Aware"
                     else "blind"),
            "turn_cap": self.cap_spin.value(),
            "stop_phrases": parse_stop_phrases(self.stop_edit.text()),
            "stop_on_agreement": self.agree_check.isChecked(),
            "review_each_hop": self.review_check.isChecked(),
        }
        try:
            save_preset(presets_path(), name, config)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            self.set_status_message(
                "Couldn't save relay preset '%s': %s" % (name, reason))
            return
        self.refresh_preset_combo(select=name)
        self.set_status_message("Relay preset '%s' saved." % name)

    def on_preset_selected(self, index):
        if index < 0:
            return
        name = self.preset_combo.currentText()
        config = load_presets(presets_path()).get(name)
        if config is None:
            # Gone (or gone bad) since the dropdown was filled --
            # say so plainly and leave the row alone.
            self.set_status_message(
                "Relay preset '%s' couldn't be loaded." % name)
            return
        self.start_picker.setCurrentText(config["start_side"])
        self.mode_picker.setCurrentText(
            "Aware" if config["mode"] == "aware" else "Blind")
        self.cap_spin.setValue(config["turn_cap"])
        self.stop_edit.setText(", ".join(config["stop_phrases"]))
        self.agree_check.setChecked(config["stop_on_agreement"])
        self.review_check.setChecked(config["review_each_hop"])
        self.set_status_message("Relay preset '%s' loaded." % name)
        self.refresh_buttons()

    def on_preset_delete(self):
        index = self.preset_combo.currentIndex()
        if index < 0:
            return
        name = self.preset_combo.currentText()
        from PySide6 import QtWidgets
        answer = QtWidgets.QMessageBox.question(
            self.window, "Delete relay preset?",
            "Delete the saved relay preset '%s'? This can't be "
            "undone." % name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            delete_preset(presets_path(), name)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            self.set_status_message(
                "Couldn't delete relay preset '%s': %s" % (name, reason))
            return
        self.refresh_preset_combo()
        self.set_status_message("Relay preset '%s' deleted." % name)
