"""Guided enrollment wizard.

Replicates Glance's enrollment flow beat-for-beat at the view layer
(sources: glance/Onboarding/OnboardingController.swift,
glance/Onboarding/OnboardingStepViews.swift,
glance/Onboarding/EnrollmentRingView.swift):

- Nine head directions in Glance's order, with Glance's exact prompt
  strings (front, up, down, left, right + diagonals).
- One instruction line whose priority is: "Face captured" > excessive
  turn > "Bring your face closer" > the current pose's prompt.
- Per-pose progress (2 samples each) and overall progress across all 9.
- Completion sequence: instructions fade, preview fades, an animated
  checkmark draws, then the wizard advances to naming on its own.
- Naming step with Glance's copy ("Name this face", "Enter a name...",
  duplicate-name and empty-name errors).

The capture state machine itself (pose bands, 500ms hold, 3-frame streak,
stall widening) lives in the engine's enroll module; the Manager only
renders what the session reports. Camera and session run on a worker
thread so a slow or stuck engine can never freeze the UI — a watchdog
reports "engine not responding" instead of hanging.

The preview is mirrored horizontally, like Glance's CameraPreviewView.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import theme
from ..camera import Camera, demo_camera_enabled
from ..engine_bridge import (
    ENROLLMENT_POSES, SAMPLES_PER_POSE, BaseEnrollmentSession, EngineBridge,
    EnrollmentSample, FrameOutcome, Identity,
)
from ..errors import CameraUnavailableError, humanize, log_exception
from ..widgets import AnimatedCheckmark, Card, PoseStrip, ProgressRing

log = logging.getLogger("mien")

POSE_ARROWS = {
    "center": "",
    "left": "\u2190",
    "right": "\u2192",
    "top": "\u2191",
    "bottom": "\u2193",
    "topLeft": "\u2196",
    "topRight": "\u2197",
    "bottomRight": "\u2198",
    "bottomLeft": "\u2199",
}

# Glance's instruction priority: complete > excessive turn > too far > pose.
# (Excessive-turn detection is engine-side; the Mien engine doesn't report
# it, so that rung is documented as a gap rather than invented here.)


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
    """Modal 9-direction enrollment. Emits `enrolled(Identity)` on success."""

    enrolled = pyqtSignal(object)

    WATCHDOG_MS = 2500
    WATCHDOG_STALE_S = 6.0

    def __init__(self, bridge: EngineBridge, existing_id: str | None = None,
                 existing_name: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.bridge = bridge
        self.existing_id = existing_id
        self._samples: list[EnrollmentSample] = []
        self._fills = [0.0] * len(ENROLLMENT_POSES)
        self._last_outcome_at = 0.0
        self._finishing = False

        self._thread: QThread | None = None
        self._worker: _FrameWorker | None = None

        self.setWindowTitle("Enroll your face")
        self.setModal(True)
        self.resize(940, 680)
        self.setStyleSheet(theme.STYLESHEET)

        self._stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self._stack.addWidget(self._build_capture_page())
        self._stack.addWidget(self._build_name_page(existing_name))

        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_watchdog)
        self._watchdog.setInterval(self.WATCHDOG_MS)

    # -- capture page ------------------------------------------------------
    def _build_capture_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.PAD_PAGE, 28, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        header = QHBoxLayout()
        title = QLabel("Enroll your face")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        if not self.bridge.using_real_engine:
            sim = QLabel("SIMULATED ENGINE")
            sim.setStyleSheet(f"color: {theme.WARNING}; font-size: 11px; font-weight: 600;")
            header.addWidget(sim)
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(28)

        # Camera cluster: preview with circular guide, checkmark on top.
        cluster = QVBoxLayout()
        self._preview_stack = QWidget()
        self._preview_stack.setFixedSize(480, 380)
        self._preview = QLabel(self._preview_stack)
        self._preview.setGeometry(0, 0, 480, 380)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            f"background: #000; border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS}px;"
        )
        self._preview.setText("Starting camera…")
        self._preview_fx = QGraphicsOpacityEffect(self._preview)
        self._preview.setGraphicsEffect(self._preview_fx)
        self._checkmark = AnimatedCheckmark(84, parent=self._preview_stack)
        self._checkmark.move((480 - 84) // 2, (380 - 84) // 2)
        self._checkmark.hide()
        cluster.addWidget(self._preview_stack)
        self._camera_note = QLabel("")
        self._camera_note.setObjectName("hint")
        self._camera_note.setWordWrap(True)
        cluster.addWidget(self._camera_note)
        cluster.addStretch(1)
        body.addLayout(cluster, stretch=3)

        # Guidance column.
        guide = QVBoxLayout()
        guide.setSpacing(theme.GAP_SMALL)

        self._instruction = QLabel("Get ready…")
        self._instruction.setStyleSheet(
            f"font-size: 20px; font-weight: 600; color: {theme.TEXT};")
        self._instruction.setWordWrap(True)
        self._instruction.setMinimumHeight(64)
        self._instruction_fx = QGraphicsOpacityEffect(self._instruction)
        self._instruction.setGraphicsEffect(self._instruction_fx)
        guide.addWidget(self._instruction)

        self._arrow = QLabel("")
        self._arrow.setStyleSheet(f"font-size: 40px; color: {theme.ACCENT};")
        self._arrow.setMinimumHeight(56)
        guide.addWidget(self._arrow)

        # Secondary per-frame quality feedback (parent brief: too dark,
        # move closer, hold still).
        self._feedback = QLabel("")
        self._feedback.setObjectName("caption")
        self._feedback.setWordWrap(True)
        self._feedback.setMinimumHeight(40)
        guide.addWidget(self._feedback)

        guide.addSpacing(8)
        self._strip = PoseStrip()
        guide.addWidget(self._strip)

        prog_row = QHBoxLayout()
        self._ring = ProgressRing(72)
        prog_row.addWidget(self._ring)
        self._progress_text = QLabel("Direction 1 of 9")
        self._progress_text.setObjectName("caption")
        prog_row.addWidget(self._progress_text)
        prog_row.addStretch(1)
        guide.addLayout(prog_row)

        guide.addStretch(1)
        tip = QLabel("Tip: sit in even light, face the screen, and turn only your head — not your shoulders.")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        guide.addWidget(tip)

        body.addLayout(guide, stretch=2)
        layout.addLayout(body)
        return page

    # -- name page (Glance's copy) -------------------------------------------
    def _build_name_page(self, preset: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.PAD_PAGE, 28, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        title = QLabel("Name this face")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        sub = QLabel("Used to tell enrolled faces apart when more than one person is set up on this PC.")
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(12)

        self._name_edit = QLineEdit()
        self._name_edit.setText(preset)
        self._name_edit.setPlaceholderText("Enter a name...")
        self._name_edit.setMaxLength(60)
        self._name_edit.returnPressed.connect(self._save_identity)
        layout.addWidget(self._name_edit)

        self._name_error = QLabel("")
        self._name_error.setStyleSheet(f"color: {theme.DANGER}; font-size: {theme.BODY_SIZE}px;")
        self._name_error.setWordWrap(True)
        layout.addWidget(self._name_error)

        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save_identity)
        row.addWidget(self._save_btn)
        layout.addLayout(row)
        return page

    # -- run -----------------------------------------------------------------
    def start(self) -> None:
        """Start the worker thread and show. Call instead of exec() alone."""
        self._thread = QThread(self)
        self._worker = _FrameWorker(self.bridge)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self._render_preview)
        self._worker.outcome_ready.connect(self._apply_outcome)
        self._worker.warmed_up.connect(self._on_warmed_up)
        self._worker.fatal.connect(self._on_fatal)
        # worker cleanup when the thread finishes
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._watchdog.start()
        self.show()

    def _on_warmed_up(self) -> None:
        if self._worker and self._worker._camera and self._worker._camera.is_demo:
            self._camera_note.setText("Simulated preview — no real camera is in use.")
        self._last_outcome_at = time.monotonic()

    def _on_fatal(self, message: str) -> None:
        self._watchdog.stop()
        if message.startswith("__CAMERA__"):
            self._show_camera_error(message[len("__CAMERA__"):], allow_demo=True)
        else:
            self._instruction.setText("Something went wrong")
            self._feedback.setText(message)
        self._stop_worker()

    def _show_camera_error(self, message: str, allow_demo: bool = False) -> None:
        self._preview.setText("")
        wrap = QLabel(message)
        wrap.setWordWrap(True)
        wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wrap.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.BODY_SIZE}px; padding: 24px;")
        self._preview.hide()
        lay = self._preview_stack.layout()
        if lay is None:
            from PyQt6.QtWidgets import QVBoxLayout as _VB
            lay = _VB(self._preview_stack)
            lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(wrap)
        self._camera_error_label = wrap
        if allow_demo and not demo_camera_enabled():
            demo_btn = QPushButton("Use simulated preview instead")
            demo_btn.clicked.connect(self._start_demo_camera)
            lay.addWidget(demo_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            self._demo_btn = demo_btn
        self._instruction.setText("Camera unavailable")
        self._feedback.setText("")

    def _start_demo_camera(self) -> None:
        import os
        os.environ["MIEN_DEMO_CAMERA"] = "1"
        # reset error UI, restart worker in demo mode
        if hasattr(self, "_camera_error_label"):
            self._camera_error_label.hide()
        if hasattr(self, "_demo_btn"):
            self._demo_btn.hide()
        self._preview.show()
        self._stop_worker()
        self._last_outcome_at = time.monotonic()
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

    # -- frame rendering ---------------------------------------------------------
    def _render_preview(self, frame) -> None:
        if self._finishing:
            return
        h, w, _ = frame.shape
        # Mirror horizontally — selfie view, like Glance's CameraPreviewView.
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_RGB888).mirrored(True, False)
        pix = QPixmap.fromImage(img).scaled(
            self._preview.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.SolidLine))
        pw, ph = pix.width(), pix.height()
        p.drawEllipse(int(pw * 0.30), int(ph * 0.22), int(pw * 0.40), int(ph * 0.56))
        p.end()
        self._preview.setPixmap(pix)

    # -- outcome -> UI (Glance's instruction priority) ------------------------------
    def _apply_outcome(self, outcome: FrameOutcome) -> None:
        if self._finishing:
            return
        self._last_outcome_at = time.monotonic()
        total_poses = len(ENROLLMENT_POSES)
        idx = max(0, min(outcome.pose_index, total_poses - 1))
        pose = ENROLLMENT_POSES[idx]

        # Progress strip: completed poses full, current pose partial.
        fills = [1.0 if i < idx else 0.0 for i in range(total_poses)]
        fills[idx] = max(0.0, min(1.0, outcome.captured_for_pose / SAMPLES_PER_POSE))
        self._strip.set_state(fills, idx)
        self._ring.setValue(outcome.overall_progress)
        self._progress_text.setText(f"Direction {idx + 1} of {total_poses}")

        if outcome.kind == "complete":
            self._finish_capture()
            return

        # Instruction priority, from OnboardingController.enrollmentInstruction:
        # excessive turn > too far > pose prompt. (The engine doesn't report
        # excessive turns — documented gap.)
        if outcome.kind == "too_far":
            self._instruction.setText("Bring your face closer")
        else:
            self._instruction.setText(pose.instruction)
        self._arrow.setText(POSE_ARROWS.get(pose.key, ""))

        # Secondary quality feedback line.
        self._feedback.setText(outcome.message)

    def _fade(self, effect: QGraphicsOpacityEffect, done=None, ms: int = 350) -> None:
        from PyQt6.QtCore import QPropertyAnimation
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        if done:
            anim.finished.connect(done)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _finish_capture(self) -> None:
        """Glance's camera-complete sequence: instructions fade, preview
        fades, checkmark draws, then auto-advance to naming."""
        self._finishing = True
        self._watchdog.stop()
        self._instruction.setText("Face captured")
        self._arrow.setText("")
        self._feedback.setText("")

        def show_checkmark():
            self._fade(self._preview_fx, ms=400)
            self._checkmark.show()
            self._checkmark.play()
            QTimer.singleShot(1400, self._collect_and_advance)

        QTimer.singleShot(500, show_checkmark)

    def _collect_and_advance(self) -> None:
        worker = self._worker
        samples: list[EnrollmentSample] = list(worker.result_samples) if worker else []
        self._stop_worker()
        self._samples = samples
        if not samples:
            self._finishing = False
            self._preview_fx.setOpacity(1.0)
            self._instruction_fx.setOpacity(1.0)
            self._checkmark.hide()
            self._instruction.setText("Look straight at the camera")
            self._feedback.setText("Nothing usable was captured — let's try that again.")
            self.start()  # restart the worker for another attempt
            return
        self._stack.setCurrentIndex(1)
        self._name_edit.setFocus()

    # -- save (Glance's name-step copy) ---------------------------------------------
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

    # -- teardown ----------------------------------------------------------------------
    def reject(self) -> None:  # noqa: N802
        self._stop_worker()
        super().reject()

    def accept(self) -> None:  # noqa: N802
        self._stop_worker()
        super().accept()
