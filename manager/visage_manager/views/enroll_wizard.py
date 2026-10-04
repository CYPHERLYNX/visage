"""Guided enrollment panel — a ditto of Glance's onboarding enroll step.

Replicates glance/Onboarding/OnboardingController.swift,
glance/Onboarding/OnboardingStepViews.swift,
glance/Onboarding/EnrollmentRingView.swift,
glance/Onboarding/EnrollmentDirectionSweep.swift and
glance/Onboarding/AnimatedCheckmark.swift:

- 320x350 black panel (51.5px bottom radius), parked top-center, 3px below
  the screen top edge — Glance's pill style for notch-less displays.
- Camera cluster: 185px circular preview inside the 200px 80-tick ring.
- 15pt medium instruction line; Glance's exact copy and priority
  (complete > excessive turn > too far > pose prompt).
- Fullscreen sweep overlay: 42 blurred ribbons sweep toward each pose
  direction (not arrows — the motion is the cue).
- Completion: ring merges to a solid ring, preview fades, checkmark draws,
  then auto-advance to naming after 3.0s total.
- Naming: 320x230 panel, Glance's copy.

The capture state machine (pose bands, 500ms hold, 3-frame streak, stall
widening) lives in engine/enroll.py; this view only renders what the
session reports. Camera and session run on a worker thread so a slow or
stuck engine can never freeze the UI.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import (
    QObject, QEasingCurve, Qt, QThread, QTimer, QPropertyAnimation,
    pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..camera import Camera
from ..engine_bridge import (
    ENROLLMENT_POSES, BaseEnrollmentSession, EngineBridge,
    EnrollmentSample, FrameOutcome,
)
from ..errors import CameraUnavailableError, humanize, log_exception
from ..widgets import AnimatedCheckmark
from ..widgets.enrollment_ring import EnrollmentRing
from ..widgets.sweep_overlay import SweepOverlay

log = logging.getLogger("visage")

# Glance's panel geometry (pill style — every Windows PC is notch-less).
PANEL_W = 320
PANEL_H_CAPTURE = 350
PANEL_H_NAME = 230

# UI pose key -> sweep direction (center has no sweep).
_SWEEP_DIRS = {
    "left": "left",
    "topLeft": "topLeft",
    "top": "up",
    "topRight": "topRight",
    "right": "right",
    "bottomRight": "bottomRight",
    "bottom": "down",
    "bottomLeft": "bottomLeft",
}


class _FrameWorker(QObject):
    """Owns the camera and the enrollment session on a background thread."""

    frame_ready = pyqtSignal(object)    # numpy RGB frame
    outcome_ready = pyqtSignal(object)  # FrameOutcome
    fatal = pyqtSignal(str)             # human-readable; worker stops
    warmed_up = pyqtSignal()

    def __init__(self, bridge: EngineBridge):
        super().__init__()
        self._bridge = bridge
        self._stop = False
        self._camera: Camera | None = None
        self._session: BaseEnrollmentSession | None = None
        self.result_samples: list = []

    @pyqtSlot()
    def run(self) -> None:
        try:
            self._session = self._bridge.create_enrollment_session()
        except Exception as exc:
            log_exception(exc, "enrollment session")
            self.fatal.emit(humanize(exc))
            return
        try:
            self._camera = Camera()
            self._camera.open()
        except CameraUnavailableError as exc:
            self.fatal.emit("__CAMERA__" + exc.full_text())
            return
        except Exception as exc:
            log_exception(exc, "camera open")
            self.fatal.emit("__CAMERA__" + humanize(exc))
            return
        self.warmed_up.emit()
        while not self._stop:
            try:
                frame = self._camera.read()
            except CameraUnavailableError as exc:
                self.fatal.emit("__CAMERA__" + exc.full_text())
                return
            except Exception as exc:
                log_exception(exc, "frame read")
                continue
            self.frame_ready.emit(frame)
            try:
                assert self._session is not None
                outcome = self._session.process_frame(frame)
            except Exception as exc:
                log_exception(exc, "process_frame")
                continue
            self.outcome_ready.emit(outcome)
            if outcome.kind == "complete":
                try:
                    assert self._session is not None
                    self.result_samples = self._session.samples()
                except Exception:
                    self.result_samples = []
                break
            QThread.msleep(90)
        self._teardown()

    def stop(self) -> None:
        self._stop = True

    def _teardown(self) -> None:
        if self._camera:
            self._camera.close()
            self._camera = None
        if self._session:
            try:
                self._session.cancel()
            except Exception:
                pass
            self._session = None


class EnrollmentWizard(QDialog):
    """Glance-style enrollment panel. Emits `enrolled(Identity)` on success."""

    enrolled = pyqtSignal(object)

    WATCHDOG_MS = 2500
    WATCHDOG_STALE_S = 6.0

    def __init__(self, bridge: EngineBridge, existing_id: str | None = None,
                 existing_name: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.bridge = bridge
        self.existing_id = existing_id
        self._samples: list[EnrollmentSample] = []
        self._last_outcome_at = 0.0
        self._finishing = False
        self._last_pose_idx = -1
        self._last_captured = 0
        self._current_pose_key = "center"
        self._sweep_pose: str | None = None

        self._thread: QThread | None = None
        self._worker: _FrameWorker | None = None
        self._sweep: SweepOverlay | None = None

        self.setWindowTitle("Enroll your face")
        # Frameless top-center panel — Glance's pill style.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(PANEL_W, PANEL_H_CAPTURE)
        self._move_to_top_center()

        # Black rounded panel (24px top, 51.5px bottom — Glance's radii).
        self._panel = QFrame(self)
        self._panel.setObjectName("enrollPanel")
        self._panel.setStyleSheet("""
            QFrame#enrollPanel {
                background: #000000;
                border-top-left-radius: 24px;
                border-top-right-radius: 24px;
                border-bottom-left-radius: 51px;
                border-bottom-right-radius: 51px;
            }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)

        self._stack = QStackedWidget(self._panel)
        panel_lay = QVBoxLayout(self._panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.addWidget(self._stack)

        self._stack.addWidget(self._build_capture_page())
        self._stack.addWidget(self._build_name_page(existing_name))

        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_watchdog)
        self._watchdog.setInterval(self.WATCHDOG_MS)

    def _move_to_top_center(self) -> None:
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QApplication.primaryScreen().geometry()
        x = geo.x() + (geo.width() - PANEL_W) // 2
        self.move(x, geo.y() + 3)

    # -- capture page (Glance's enroll layout) --------------------------------
    def _build_capture_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 30, 0, 30)  # top 30, bottom 30
        layout.setSpacing(0)

        # Close button: 28x28, top-trailing, inset 18.
        self._close_btn = QPushButton("\u00d7", page)
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.move(PANEL_W - 18 - 28, 18)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: rgba(255, 255, 255, 204); font-size: 15px; font-weight: 600;
            }
            QPushButton:hover { color: #ffffff; }
        """)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)

        # Camera cluster: 240x240 box, ring + circular preview inside.
        cluster = QWidget(page)
        cluster.setFixedSize(240, 240)
        self._ring = EnrollmentRing(cluster)
        self._ring.move(0, 0)

        self._preview = QLabel(cluster)
        self._preview.setFixedSize(185, 185)
        self._preview.move((240 - 185) // 2, (240 - 185) // 2)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background: #0a0a0a; color: #949494; font-size: 12px;")
        self._preview.setText("Starting camera\u2026")
        self._preview.setMask(QRegion(0, 0, 185, 185, QRegion.RegionType.Ellipse))
        self._preview_fx = QGraphicsOpacityEffect(self._preview)
        self._preview.setGraphicsEffect(self._preview_fx)

        # Too-far chevron: double-chevron-up, 32pt, over the preview.
        self._chevron = QLabel("\u2303\u2303", cluster)
        self._chevron.setStyleSheet(
            "color: #ffffff; font-size: 32px; font-weight: 600; background: transparent;")
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.setFixedSize(185, 60)
        self._chevron.move((240 - 185) // 2, (240 - 60) // 2 - 20)
        self._chevron.hide()
        self._chevron_fx = QGraphicsOpacityEffect(self._chevron)
        self._chevron.setGraphicsEffect(self._chevron_fx)

        # Checkmark: 70x59, centered, hidden until completion.
        self._checkmark = AnimatedCheckmark(70, 59, parent=cluster)
        self._checkmark.move((240 - 70) // 2, (240 - 59) // 2 + 4)
        self._checkmark.hide()

        layout.addWidget(cluster, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)

        # Instruction: 15pt medium, white, centered, h-pad 20.
        self._instruction = QLabel("Get ready\u2026", page)
        self._instruction.setStyleSheet(
            "color: #ffffff; font-size: 15px; font-weight: 600; background: transparent;")
        font = self._instruction.font()
        font.setWeight(600)  # DemiBold ~ medium
        self._instruction.setFont(font)
        self._instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._instruction.setWordWrap(True)
        self._instruction.setContentsMargins(20, 0, 20, 0)
        self._instruction_fx = QGraphicsOpacityEffect(self._instruction)
        self._instruction.setGraphicsEffect(self._instruction_fx)
        layout.addWidget(self._instruction)
        layout.addStretch(1)
        return page

    # -- name page --------------------------------------------------------------
    def _build_name_page(self, preset: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 32, 28, 32)
        layout.setSpacing(12)

        title = QLabel("Name this face")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._name_edit = QLineEdit()
        self._name_edit.setText(preset)
        self._name_edit.setPlaceholderText("Enter a name...")
        self._name_edit.setMaxLength(60)
        self._name_edit.setStyleSheet("""
            QLineEdit {
                background: #1e1e1e; color: #ffffff; border: 1px solid #333;
                border-radius: 19px; padding: 0 16px; font-size: 13px; min-height: 38px;
            }
            QLineEdit:focus { border: 1px solid #3499ff; }
        """)
        self._name_edit.returnPressed.connect(self._save_identity)
        layout.addWidget(self._name_edit)

        self._name_error = QLabel("")
        self._name_error.setStyleSheet("color: #ff453a; font-size: 12px;")
        self._name_error.setWordWrap(True)
        self._name_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._name_error)

        layout.addStretch(1)
        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedSize(160, 38)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: #3499ff; color: #ffffff; border: none;
                border-radius: 19px; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #4aa5ff; }
            QPushButton:disabled { background: #1e1e1e; color: #666; }
        """)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_identity)
        layout.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        return page

    # -- run --------------------------------------------------------------------
    def start(self) -> None:
        """Start the worker thread, sweep overlay, and show."""
        self._sweep = SweepOverlay()
        self._sweep.show()
        # Panel sits above the sweep.
        self.show()
        self.raise_()

        self._thread = QThread(self)
        self._worker = _FrameWorker(self.bridge)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self._render_preview)
        self._worker.outcome_ready.connect(self._apply_outcome)
        self._worker.warmed_up.connect(self._on_warmed_up)
        self._worker.fatal.connect(self._on_fatal)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._watchdog.start()

    def _on_warmed_up(self) -> None:
        self._last_outcome_at = time.monotonic()

    def _on_fatal(self, message: str) -> None:
        self._watchdog.stop()
        self._stop_sweep()
        if message.startswith("__CAMERA__"):
            self._instruction.setText("Camera unavailable")
            self._preview.setText(message[len("__CAMERA__"):][:120])
        else:
            self._instruction.setText("Something went wrong")
        self._stop_worker()

    def _stop_sweep(self) -> None:
        if self._sweep:
            try:
                self._sweep.stop()
            except Exception:
                pass
            self._sweep = None
        self._sweep_pose = None

    def _stop_worker(self) -> None:
        self._watchdog.stop()
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None

    def _check_watchdog(self) -> None:
        if self._finishing or not self._worker:
            return
        if self._last_outcome_at and time.monotonic() - self._last_outcome_at > self.WATCHDOG_STALE_S:
            self._last_outcome_at = 0.0  # fire once
            self._on_fatal(
                "The face engine stopped responding.\n\n"
                "Close any other app using the camera and try again.")

    # -- frame rendering --------------------------------------------------------
    def _render_preview(self, frame) -> None:
        if self._finishing:
            return
        h, w, _ = frame.shape
        # Mirror horizontally — selfie view, like Glance's CameraPreviewView.
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_RGB888).mirrored(True, False)
        pix = QPixmap.fromImage(img).scaled(
            185, 185, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        # Center-crop to the 185px circle.
        x = (pix.width() - 185) // 2
        y = (pix.height() - 185) // 2
        self._preview.setPixmap(pix.copy(x, y, 185, 185))

    # -- outcome -> UI (Glance's instruction priority) ---------------------------
    def _apply_outcome(self, outcome: FrameOutcome) -> None:
        if self._finishing:
            return
        self._last_outcome_at = time.monotonic()
        total_poses = len(ENROLLMENT_POSES)
        idx = max(0, min(outcome.pose_index, total_poses - 1))
        pose = ENROLLMENT_POSES[idx]
        self._current_pose_key = pose.key

        # Ring: light sectors for completed poses.
        sectors = {
            ENROLLMENT_POSES[i].compass_angle
            for i in range(idx)
            if ENROLLMENT_POSES[i].compass_angle is not None
        }
        self._ring.set_captured_sectors(sectors)

        # Center pulse when a center sample lands.
        if idx != self._last_pose_idx:
            self._last_pose_idx = idx
            self._last_captured = 0
        if idx == 0 and outcome.captured_for_pose > self._last_captured:
            self._ring.pulse_center()
        self._last_captured = outcome.captured_for_pose

        # Live head-turn indicator.
        self._ring.set_head_turn(outcome.head_turn_angle, outcome.head_turn_progress)

        if outcome.kind == "complete":
            self._finish_capture()
            return

        # Instruction priority: excessive turn > too far > pose prompt.
        if outcome.kind == "excessive_turn":
            self._set_instruction(outcome.message or "Too far")
            self._chevron.hide()
        elif outcome.kind == "too_far":
            self._set_instruction("Bring your face closer")
            self._chevron.show()
            self._stop_sweep()
        else:
            self._set_instruction(pose.instruction)
            self._chevron.hide()

        # Sweep: per pose, after 0.36s stillness; suppressed for center.
        sweep_dir = _SWEEP_DIRS.get(pose.key)
        if outcome.kind != "too_far" and sweep_dir and sweep_dir != self._sweep_pose:
            self._sweep_pose = sweep_dir
            QTimer.singleShot(360, self._maybe_play_sweep)

    def _set_instruction(self, text: str) -> None:
        if self._instruction.text() != text:
            self._instruction.setText(text)

    def _maybe_play_sweep(self) -> None:
        if self._finishing or not self._sweep or not self._sweep_pose:
            return
        if _SWEEP_DIRS.get(self._current_pose_key) != self._sweep_pose:
            return  # pose moved on; don't play a stale cue
        try:
            self._sweep.play(self._sweep_pose)
        except Exception:
            log_exception("sweep play")

    # -- completion (Glance's camera-complete sequence) --------------------------
    def _finish_capture(self) -> None:
        self._finishing = True
        self._watchdog.stop()
        self._stop_sweep()
        self._close_btn.hide()
        self._ring.set_complete()
        self._set_instruction("Face captured")
        self._chevron.hide()
        # +0.35s: preview fades (0.4s).
        QTimer.singleShot(350, lambda: self._fade(self._preview_fx, ms=400))
        # +0.95s: checkmark draws (0.28s).
        QTimer.singleShot(950, self._show_checkmark)
        # +3.0s total: stop camera, advance to naming.
        QTimer.singleShot(3000, self._collect_and_advance)

    def _show_checkmark(self) -> None:
        if not self._finishing:
            return
        self._checkmark.show()
        self._checkmark.play()

    def _fade(self, effect: QGraphicsOpacityEffect, ms: int = 350) -> None:
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _collect_and_advance(self) -> None:
        worker = self._worker
        samples: list[EnrollmentSample] = list(worker.result_samples) if worker else []
        self._stop_worker()
        self._samples = samples
        if not samples:
            self._reset_for_retry()
            return
        self._goto_name_page()

    def _reset_for_retry(self) -> None:
        self._finishing = False
        self._preview_fx.setOpacity(1.0)
        self._instruction_fx.setOpacity(1.0)
        self._checkmark.hide()
        self._ring.reset()
        self._close_btn.show()
        self._last_pose_idx = -1
        self._last_captured = 0
        self._set_instruction("Look straight at the camera")
        self.start()  # restart the worker for another attempt

    def _goto_name_page(self) -> None:
        self._stack.setCurrentIndex(1)
        self._animate_height(PANEL_H_NAME)
        self._name_edit.setFocus()
        # Autofocus delay after step change (Glance: 0.48s).
        QTimer.singleShot(480, self._name_edit.setFocus)

    def _animate_height(self, target_h: int) -> None:
        anim = QPropertyAnimation(self, b"size", self)
        anim.setDuration(420)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.setStartValue(self.size())
        anim.setEndValue(self.size().__class__(PANEL_W, target_h))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # -- save (Glance's name-step copy) ------------------------------------------
    def _save_identity(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._name_error.setText("Enter a name.")
            return
        if self.bridge.name_is_taken(name, excluding=self.existing_id):
            self._name_error.setText(f'A face named "{name}" is already enrolled.')
            return
        self._save_btn.setEnabled(False)
        try:
            identity = self.bridge.commit_enrollment(name, self._samples, self.existing_id)
        except Exception as exc:
            log_exception(exc, "commit enrollment")
            self._name_error.setText(humanize(exc).split("\n\n")[0])
            self._save_btn.setEnabled(True)
            return
        self.enrolled.emit(identity)
        self.accept()

    # -- teardown -------------------------------------------------------------------
    def reject(self) -> None:  # noqa: N802
        self._stop_sweep()
        self._stop_worker()
        super().reject()

    def accept(self) -> None:  # noqa: N802
        self._stop_sweep()
        self._stop_worker()
        super().accept()
