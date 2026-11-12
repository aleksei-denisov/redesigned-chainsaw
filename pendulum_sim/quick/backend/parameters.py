"""QML context objects ``model`` and ``controller``: the parameter screens' editors.

Both wrap shared editor forms (``pendulum_sim/session/forms.py``) in list models and
turn each edit into a session edit (``Sim.session``), whose operations go to the physics
process. Decisions – what a structural change does to the controller, restoring gains,
LQR seeding – are the shared rules; texts are the shared texts.
"""

from __future__ import annotations

import copy
import math

import numpy as np
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ...session import texts
from ...session.forms import ControllerForm, PlantForm, SimulationForm
from .formmodel import FormModel
from .simulation import SimulationBridge

OUTPUT_HZ = 5.0


class ModelBridge(QObject):
    """Pendulum, cart, actuator, segments and simulation settings (the *Model* screen)."""

    def __init__(self, sim: SimulationBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.plant_form = PlantForm()
        self.sim_form = SimulationForm()
        self._fill()
        self._plant_model = FormModel(self.plant_form, self._edit_plant, visible=self.plant_form.visible,
                                      editable=self.plant_form.editable, note=self._note, parent=self)
        self._sim_model = FormModel(self.sim_form, self._edit_simulation, expanded={"simulation": True},
                                    parent=self)          # (its own card on the Model screen)
        sim.configChanged.connect(self._reload)

    plantModel = Property(QObject, lambda self: self._plant_model, constant=True)
    simulationModel = Property(QObject, lambda self: self._sim_model, constant=True)

    def _note(self, section_key: str) -> str:
        if section_key == "actuator":
            return self.plant_form.actuator_note()
        section = next((s for s in self.plant_form.sections if s.key == section_key), None)
        return section.note if section is not None else ""

    def _fill(self) -> None:
        cfg = self.sim.session.config
        self.plant_form.set_plant(copy.deepcopy(cfg.plant))
        self.sim_form.set_simulation(copy.deepcopy(cfg.simulation))

    def _reload(self) -> None:
        self._fill()
        self._plant_model.refresh()
        self._sim_model.refresh()

    def _edit_plant(self, key: str, value) -> None:
        plant, structural = self.plant_form.set(key, value)
        try:
            change = self.sim.session.change_plant(plant, structural)
        except ValueError as exc:
            self.sim.message.emit("warning", f"⚠ {exc}")
            self._plant_model.update_values()
            return
        self.sim.apply_operations(change.operations)
        if change.reset_if_idle:
            self.sim.apply_operations([("reset_if_idle",)])
        if structural:
            self.sim.structure_changed()
            self.sim.controllerChanged.emit()
            self.sim.message.emit("info", texts.STRUCTURE_CHANGED)
            self._plant_model.refresh()          # sections shown change (cart, segments)
        else:
            self._plant_model.update_values()    # e.g. the computed rod inertia
        self.sim.plantChanged.emit(structural)

    def _edit_simulation(self, key: str, value) -> None:
        settings = self.sim_form.set(key, value)
        try:
            self.sim.apply_operations(self.sim.session.change_simulation(settings))
        except ValueError as exc:
            self.sim.message.emit("warning", f"⚠ {exc}")
        self._sim_model.update_values()


class ControllerBridge(QObject):
    """Controller settings, PID loops, LQR weights and the tuning actions (*Controller* screen)."""

    outputChanged = Signal()

    def __init__(self, sim: SimulationBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.form = ControllerForm()
        self.form.set_config(copy.deepcopy(sim.session.config.controller))
        self._model = FormModel(self.form, self._edit, note=self._note, parent=self)
        self._output = ""
        self._loop_outputs: dict[str, str] = {}
        sim.controllerChanged.connect(self._reload)
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / OUTPUT_HZ))
        self._timer.timeout.connect(self._poll_output)
        self.active = False                       # live outputs only while the screen is shown

    model = Property(QObject, lambda self: self._model, constant=True)
    outputText = Property(str, lambda self: self._output, notify=outputChanged)

    def _note(self, section_key: str) -> str:
        if section_key.startswith("loop"):
            return self._loop_outputs.get(section_key, "")
        if section_key == "controller":
            return self._output
        return ""

    def _reload(self) -> None:
        rebuilt = self.form.set_config(copy.deepcopy(self.sim.session.config.controller))
        if rebuilt:
            self._model.refresh()
        else:
            self._model.update_values()

    def _edit(self, key: str, value) -> None:
        ctrl = self.form.set(key, value)
        if ctrl is None:                          # an LQR weight: used by the next seeding
            return
        try:
            self.sim.apply_operations(self.sim.session.change_controller(ctrl))
        except ValueError as exc:
            self.sim.message.emit("warning", f"⚠ {exc}")
        self._model.update_values()

    # ---------------------------------------------------------- actions
    @Slot()
    def seedLqr(self) -> None:  # noqa: N802 - QML API
        try:
            _ctrl, ops = self.sim.session.seed_lqr(self.form.weights(), base=self.form.config())
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.sim.message.emit("error", f"The LQR design could not be computed for this plant:\n{exc}")
            return
        self.sim.apply_operations(ops)
        self._reload()
        self.sim.message.emit("info", texts.LQR_SEEDED)

    @Slot()
    def restoreGains(self) -> None:  # noqa: N802 - QML API
        try:
            _ctrl, ops = self.sim.session.restore_gains()
        except ValueError as exc:
            self.sim.message.emit("warning", f"⚠ {exc}")
            return
        self.sim.apply_operations(ops)
        self._reload()
        self.sim.message.emit("info", texts.GAINS_RESTORED)

    @Slot()
    def resetIntegrators(self) -> None:  # noqa: N802 - QML API
        self.sim.apply_operations([("reset_integrators",)])
        self.sim.message.emit("info", "Integrators reset")

    def apply_tuning(self, result) -> None:
        self.sim.apply_operations(self.sim.session.apply_tuning(result))
        self._reload()
        self.sim.pushForceChanged.emit()
        self.sim.message.emit("info", texts.tuning_applied(result))

    def use_run_gains(self, run_config) -> bool:
        try:
            _ctrl, ops = self.sim.session.use_run_gains(run_config)
        except ValueError as exc:
            self.sim.message.emit("warning", str(exc))
            return False
        self.sim.apply_operations(ops)
        self._reload()
        return True

    # ------------------------------------------------------ live output
    @Slot(bool)
    def setActive(self, on: bool) -> None:  # noqa: N802 - QML API
        """The screen is shown: read the controller's outputs a few times per second."""
        self.active = on
        if on:
            self._timer.start()
            self._poll_output()
        else:
            self._timer.stop()

    def _poll_output(self) -> None:
        src = self.sim.source
        command, unsat = src.latest("command"), src.latest("unsaturated")
        if math.isnan(command):
            return
        self._output = f"Output: command {command:+.3f} · unsaturated {unsat:+.3f}"
        for k, signal in enumerate(self.form.loop_signals()):
            if k >= 4:
                break
            error, p, i, d = (src.latest(f"{name}{k}") for name in ("error", "p", "i", "d"))
            e = f"{math.degrees(error):+.2f}°" if signal != "cart" else f"{error:+.4f} m"
            self._loop_outputs[f"loop{k}"] = f"live: error {e} · P {p:+.3g} · I {i:+.3g} · D {d:+.3g}"
        self.outputChanged.emit()
        self._model.update_values()
