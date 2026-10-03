"""Face Lab: hidden diagnostics console.

Replicates the sections of Glance's Face Lab (FaceLabController.swift /
FaceLabView.swift) that the Manager can genuinely provide:

- Embedder status (which model is active)
- Vault / identity-store state
- Camera preview with start/stop and error reporting
- Detection: faces found, top score, box size, embedding dimension
- Manual sample capture into a named identity
- Identities with per-sample disclosure (index, pose tag, captured time)
- Recognize: Identify against enrolled identities with centroid AND
  max-sample similarity, both gated by the threshold slider — exactly
  the pipeline's scoring rule (no runner-up margin check)
- Threshold calibration: tag genuine/impostor scores, suggested
  threshold (midpoint), overlap warning, strip chart
- Liveness: the five cues with real thresholds from the engine's
  LivenessTuning; live readings need an active unlock scan, which the
  Manager doesn't run, so readings report "no signal" honestly
- Timestamped log, capped at 200 lines

Revealed via five consecutive clicks on the About page logo.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSlider, QTextEdit, QVBoxLayout, QWidget,
)

from .. import theme
from ..camera import Camera
from ..engine_bridge import CUE_DEFINITIONS, EngineBridge
from ..errors import EngineUnavailableError, VisageError, humanize, log_exception
from ..widgets import Card, CueBar

log = logging.getLogger("visage")

LOG_CAP = 200


class _LabWorker(QObject):
    frame_ready = pyqtSignal(object)
    stats_ready = pyqtSignal(dict)
    log_line = pyqtSignal(str)
    identify_done = pyqtSignal(dict)
    capture_done = pyqtSignal(str)
    fatal = pyqtSignal(str)

    def __init__(self, bridge: EngineBridge):
        super().__init__()
        self._bridge = bridge
        self._stop = False
        self._camera: Camera | None = None
        self._latest = None
        self._last_stats_at = 0.0
        self._identify_threshold: float | None = None
        self._capture_name: str | None = None

    @pyqtSlot()
    def run(self) -> None:
        try:
            self._camera = Camera()
            self._camera.open()
        except Exception as exc:
            log_exception(exc, "face lab camera")
            self.fatal.emit(humanize(exc).split("\n\n")[0])
            return
        import time
        self.log_line.emit("Camera started.")
        while not self._stop:
            try:
                frame = self._camera.read()
            except Exception as exc:
                self.fatal.emit(humanize(exc).split("\n\n")[0])
                return
            self._latest = frame
            self.frame_ready.emit(frame)
            now = time.monotonic()
            if now - self._last_stats_at > 0.6:
                self._last_stats_at = now
                self.stats_ready.emit(self._detect_stats(frame))
            if self._identify_threshold is not None:
                threshold, self._identify_threshold = self._identify_threshold, None
                self.identify_done.emit(self._do_identify(threshold))
            if self._capture_name is not None:
                name, self._capture_name = self._capture_name, None
                self.capture_done.emit(self._do_capture(name))
            QThread.msleep(120)
        self._camera.close()
        self._camera = None
        self.log_line.emit("Camera stopped.")

    def _detect_stats(self, frame) -> dict:
        try:
            faces = self._bridge.detect_live(frame)
        except EngineUnavailableError:
            return {"simulated": True}
        except Exception:
            return {"faces": [], "error": True}
        return {"faces": faces}

    def _do_identify(self, threshold: float) -> dict:
        if self._latest is None:
            return {"error": "No frame yet — wait for the preview."}
        try:
            return self._bridge.identify_live(self._latest, threshold)
        except VisageError as exc:
            return {"error": exc.full_text().split("\n\n")[0]}
        except Exception as exc:
            log_exception(exc, "identify")
            return {"error": humanize(exc).split("\n\n")[0]}

    def _do_capture(self, name: str) -> str:
        if self._latest is None:
            return "No frame yet — wait for the preview."
        try:
            self._bridge.add_live_sample(name, self._latest)
            return f'Captured sample for "{name}".'
        except VisageError as exc:
            return f"Couldn't save sample: {exc.full_text().split(chr(10)+chr(10))[0]}"
        except Exception as exc:
            log_exception(exc, "manual capture")
            return f"Couldn't save sample: {humanize(exc).split(chr(10)+chr(10))[0]}"

    @pyqtSlot(float)
    def identify(self, threshold: float) -> None:
        self._identify_threshold = threshold

    @pyqtSlot(str)
    def capture(self, name: str) -> None:
        self._capture_name = name

    def stop(self) -> None:
        self._stop = True


class CalibrationChart(QWidget):
    """Strip chart: genuine (green) vs impostor (red) similarity dots,
    dashed threshold line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(96)
        self._samples: list[tuple[float, bool]] = []
        self._threshold = 0.4

    def set_data(self, samples: list[tuple[float, bool]], threshold: float) -> None:
        self._samples = samples
        self._threshold = threshold
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, pad = self.width(), self.height(), 14
        span = w - 2 * pad

        def x_of(sim: float) -> int:
            t = (max(-1.0, min(1.0, sim)) + 1.0) / 2.0
            return int(pad + t * span)

        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(pad, h // 2, w - pad, h // 2)
        # threshold marker
        tx = x_of(self._threshold)
        p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine))
        p.drawLine(tx, 8, tx, h - 8)
        for sim, genuine in self._samples:
            y = h // 2 - 18 if genuine else h // 2 + 18
            p.setBrush(QColor(theme.SUCCESS if genuine else theme.DANGER))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x_of(sim) - 4, y - 4, 8, 8)
        p.setPen(QColor(theme.TEXT_TERTIARY))
        p.drawText(pad, h - 2, "-1")
        p.drawText(w - pad - 12, h - 2, "+1")
        p.end()


class FaceLabPage(QWidget):
    def __init__(self, bridge: EngineBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._thread: QThread | None = None
        self._worker: _LabWorker | None = None
        self._log_count = 0
        self._calibration: list[tuple[float, bool]] = []  # (similarity, genuine)
        self._recognition_rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        head = QHBoxLayout()
        title = QLabel("Face Lab")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)
        sub = QLabel("Diagnostics console. Values are live from the engine; nothing here changes your setup.")
        sub.setObjectName("pageSubtitle")
        layout.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setSpacing(theme.GAP)
        hl.addWidget(self._status_card())
        hl.addWidget(self._preview_card())
        hl.addWidget(self._detection_card())
        hl.addWidget(self._enroll_card())
        hl.addWidget(self._identities_card())
        hl.addWidget(self._recognize_card())
        hl.addWidget(self._calibration_card())
        hl.addWidget(self._liveness_card())
        hl.addWidget(self._log_card())
        hl.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, stretch=1)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_identities)
        self._poll.setInterval(4000)

    # -- helpers ---------------------------------------------------------------
    def _log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log_view.append(f"[{stamp}] {text}")
        self._log_count += 1
        if self._log_count > LOG_CAP:
            # trim from the top
            cursor = self._log_view.textCursor()
            cursor.movePosition(cursor.MoveMode.Start)
            cursor.movePosition(cursor.MoveMode.Down, cursor.MoveMode.KeepAnchor, self._log_count - LOG_CAP)
            cursor.removeSelectedText()
            self._log_count = LOG_CAP

    # -- embedder / vault status ---------------------------------------------------
    def _status_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Engine")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        row = QHBoxLayout()
        dot = QLabel("●")
        ok = self.bridge.using_real_engine
        dot.setStyleSheet(f"color: {theme.SUCCESS if ok else theme.WARNING}; font-size: 10px;")
        row.addWidget(dot)
        row.addWidget(QLabel(f"Embedder: {self.bridge.embedder_name()}"))
        row.addStretch(1)
        lay.addLayout(row)

        vault_row = QHBoxLayout()
        vdot = QLabel("●")
        has_vault = self.bridge.has_credentials()
        vdot.setStyleSheet(f"color: {theme.SUCCESS if has_vault else theme.TEXT_TERTIARY}; font-size: 10px;")
        vault_row.addWidget(vdot)
        vault_row.addWidget(QLabel(
            "Password vault: " + ("a password is stored" if has_vault else "no password stored")))
        vault_row.addStretch(1)
        lay.addLayout(vault_row)

        store_row = QHBoxLayout()
        sdot = QLabel("●")
        try:
            n = len(self.bridge.list_identities())
            sdot.setStyleSheet(f"color: {theme.SUCCESS}; font-size: 10px;")
            label = f"Identity store: readable, {n} enrolled"
        except Exception:
            sdot.setStyleSheet(f"color: {theme.DANGER}; font-size: 10px;")
            label = "Identity store: unreadable"
        store_row.addWidget(sdot)
        store_row.addWidget(QLabel(label))
        store_row.addStretch(1)
        lay.addLayout(store_row)
        return card

    # -- preview ----------------------------------------------------------------------
    def _preview_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Camera Preview")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        row = QHBoxLayout()
        self._cam_dot = QLabel("●")
        self._cam_dot.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; font-size: 10px;")
        row.addWidget(self._cam_dot)
        self._cam_state = QLabel("Stopped")
        row.addWidget(self._cam_state)
        row.addStretch(1)
        self._cam_btn = QPushButton("Start")
        self._cam_btn.clicked.connect(self._toggle_camera)
        row.addWidget(self._cam_btn)
        lay.addLayout(row)

        self._cam_error = QLabel("")
        self._cam_error.setStyleSheet(f"color: {theme.DANGER}; font-size: {theme.BODY_SIZE}px;")
        self._cam_error.setWordWrap(True)
        lay.addWidget(self._cam_error)

        self._preview = QLabel()
        self._preview.setFixedHeight(260)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            f"background: #000; border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_SMALL}px;")
        self._preview.setText("Preview off")
        lay.addWidget(self._preview)
        return card

    def _toggle_camera(self) -> None:
        if self._thread:
            self._stop_worker()
        else:
            self._cam_error.setText("")
            self._thread = QThread(self)
            self._worker = _LabWorker(self.bridge)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.frame_ready.connect(self._render_frame)
            self._worker.stats_ready.connect(self._render_stats)
            self._worker.log_line.connect(self._log)
            self._worker.identify_done.connect(self._render_identify)
            self._worker.capture_done.connect(lambda msg: self._log(msg))
            self._worker.fatal.connect(self._on_cam_fatal)
            self._thread.finished.connect(self._worker.deleteLater)
            self._thread.finished.connect(self._thread.deleteLater)
            self._thread.start()
            self._cam_dot.setStyleSheet(f"color: {theme.SUCCESS}; font-size: 10px;")
            self._cam_state.setText("Running")
            self._cam_btn.setText("Stop")
            self._poll.start()

    def _on_cam_fatal(self, message: str) -> None:
        self._cam_error.setText(message)
        self._log(f"Camera error: {message}")
        self._stop_worker()

    def _stop_worker(self) -> None:
        self._poll.stop()
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None
        self._cam_dot.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; font-size: 10px;")
        self._cam_state.setText("Stopped")
        self._cam_btn.setText("Start")
        self._preview.setText("Preview off")
        self._preview.setPixmap(QPixmap())

    def _render_frame(self, frame) -> None:
        h, w, _ = frame.shape
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_RGB888).mirrored(True, False)
        pix = QPixmap.fromImage(img).scaled(
            self._preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._preview.setPixmap(pix)

    # -- detection ----------------------------------------------------------------------
    def _detection_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Detection")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        self._det_faces = QLabel("Faces detected: —")
        lay.addWidget(self._det_faces)
        self._det_score = QLabel("Top score: —")
        self._det_score.setObjectName("caption")
        lay.addWidget(self._det_score)
        self._det_box = QLabel("Box: —")
        self._det_box.setObjectName("caption")
        lay.addWidget(self._det_box)
        emb = QLabel("Embedding: 512 numbers")
        emb.setObjectName("caption")
        lay.addWidget(emb)
        return card

    def _render_stats(self, stats: dict) -> None:
        if stats.get("simulated"):
            self._det_faces.setText("Faces detected: — (simulated engine)")
            return
        faces = stats.get("faces", [])
        self._det_faces.setText(f"Faces detected: {len(faces)}")
        if faces:
            top = faces[0]
            self._det_score.setText(f"Top score: {top['score']:.2f}")
            x, y, w, h = top["box"]
            self._det_box.setText(f"Box: {w}×{h}px at ({x}, {y})")
        else:
            self._det_score.setText("Top score: —")
            self._det_box.setText("Box: —")

    # -- manual enroll ---------------------------------------------------------------------
    def _enroll_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Capture Sample")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        cap = QLabel("Captures one embedding from the live preview into a named identity.")
        cap.setObjectName("caption")
        lay.addWidget(cap)
        row = QHBoxLayout()
        self._cap_name = QLineEdit()
        self._cap_name.setPlaceholderText("Name")
        row.addWidget(self._cap_name, stretch=1)
        btn = QPushButton("Capture")
        btn.clicked.connect(self._on_capture)
        btn.setEnabled(False)
        self._cap_btn = btn
        row.addWidget(btn)
        lay.addLayout(row)
        # enable capture only while preview runs
        return card

    def _on_capture(self) -> None:
        name = self._cap_name.text().strip()
        if not name:
            self._log("Enter a name before capturing a sample.")
            return
        if self._worker and self._thread:
            # queued to the worker thread
            from PyQt6.QtCore import QMetaObject, Q_ARG
            QMetaObject.invokeMethod(self._worker, "capture",
                                     Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(str, name))
        else:
            self._log("Start the preview first.")

    # -- identities ----------------------------------------------------------------------------
    def _identities_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Identities")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        self._ident_host = QVBoxLayout()
        lay.addLayout(self._ident_host)
        self._refresh_identities()
        return card

    def _refresh_identities(self) -> None:
        if not self.isVisible():
            return
        while self._ident_host.count():
            item = self._ident_host.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            identities = self.bridge.list_identities()
        except Exception as exc:
            err = QLabel(humanize(exc).split("\n\n")[0])
            err.setStyleSheet(f"color: {theme.DANGER};")
            self._ident_host.addWidget(err)
            return
        if not identities:
            none = QLabel("No identities enrolled yet.")
            none.setObjectName("hint")
            self._ident_host.addWidget(none)
            return
        for ident in identities:
            wrap = QWidget()
            vl = QVBoxLayout(wrap)
            vl.setContentsMargins(0, 4, 0, 4)
            title = QLabel(f"{ident.name} — {ident.sample_count} samples")
            title.setStyleSheet("font-weight: 600;")
            vl.addWidget(title)
            for i, s in enumerate(ident.samples):
                q = f"{s.quality:.0%}" if s.quality is not None else "—"
                row = QLabel(f"  {i + 1:2d}  {s.pose or 'untagged':12s}  quality {q}")
                row.setStyleSheet("font-family: Consolas, monospace; font-size: 11px; "
                                  f"color: {theme.TEXT_SECONDARY};")
                vl.addWidget(row)
            self._ident_host.addWidget(wrap)

    # -- recognize ----------------------------------------------------------------------------------
    def _recognize_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Recognize")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        row = QHBoxLayout()
        self._identify_btn = QPushButton("Identify")
        self._identify_btn.clicked.connect(self._on_identify)
        row.addWidget(self._identify_btn)
        row.addStretch(1)
        self._thr_label = QLabel("")
        self._thr_label.setObjectName("caption")
        row.addWidget(self._thr_label)
        self._thr_slider = QSlider(Qt.Orientation.Horizontal)
        self._thr_slider.setMinimum(-100)
        self._thr_slider.setMaximum(100)
        default_thr = self.bridge.match_threshold_default()
        self._thr_slider.setValue(int(default_thr * 100))
        self._thr_slider.setFixedWidth(160)
        self._thr_slider.valueChanged.connect(
            lambda v: self._thr_label.setText(f"Threshold: {v / 100:.2f}"))
        self._thr_label.setText(f"Threshold: {default_thr:.2f}")
        row.addWidget(self._thr_slider)
        lay.addLayout(row)

        self._rec_verdict = QLabel("")
        self._rec_verdict.setWordWrap(True)
        lay.addWidget(self._rec_verdict)
        self._rec_host = QVBoxLayout()
        lay.addLayout(self._rec_host)
        return card

    def _on_identify(self) -> None:
        if self._worker and self._thread:
            from PyQt6.QtCore import QMetaObject, Q_ARG
            QMetaObject.invokeMethod(self._worker, "identify",
                                     Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(float, self._thr_slider.value() / 100))
        else:
            self._rec_verdict.setText("Start the preview first.")

    def _render_identify(self, result: dict) -> None:
        while self._rec_host.count():
            item = self._rec_host.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if "error" in result:
            self._rec_verdict.setText(result["error"])
            self._log(f"Identify: {result['error']}")
            return
        rows = result.get("scored", [])
        self._recognition_rows = rows
        best = result.get("best")
        if best:
            self._rec_verdict.setText(f"Best match: {best}")
            self._rec_verdict.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: 600;")
            verdict = QLabel("MATCH")
            verdict.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: 700;")
        elif rows:
            first = rows[0]
            self._rec_verdict.setText(f"Closest: {first['name']}")
            self._rec_verdict.setStyleSheet(f"color: {theme.DANGER}; font-weight: 600;")
            verdict = QLabel("NO MATCH")
            verdict.setStyleSheet(f"color: {theme.DANGER}; font-weight: 700;")
        else:
            self._rec_verdict.setText("No enrolled identities to compare against.")
            self._rec_verdict.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
            verdict = None
        if verdict:
            self._rec_host.addWidget(verdict)
        for r in rows:
            line = QLabel(f"{r['name']}: centroid {r['centroid']:.3f}   max {r['max']:.3f}")
            line.setObjectName("caption")
            line.setStyleSheet("font-family: Consolas, monospace;")
            self._rec_host.addWidget(line)
        if rows:
            top = rows[0]
            self._log(f"Identify: best = {top['name']} centroid={top['centroid']:.3f} "
                      f"max={top['max']:.3f} -> {'MATCH' if best else 'no match'}")

    # -- calibration -----------------------------------------------------------------------------------
    def _calibration_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Threshold Calibration")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        cap = QLabel(
            "Run Identify, then tag whether that was really you or someone else — across "
            "lighting, angle, and expression — to see where the two score distributions fall.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        lay.addWidget(cap)

        row = QHBoxLayout()
        genuine_btn = QPushButton("Mark Genuine (this is me)")
        genuine_btn.clicked.connect(lambda: self._tag_calibration(True))
        row.addWidget(genuine_btn)
        impostor_btn = QPushButton("Mark Impostor (not me)")
        impostor_btn.clicked.connect(lambda: self._tag_calibration(False))
        row.addWidget(impostor_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("ghost")
        clear_btn.clicked.connect(self._clear_calibration)
        row.addWidget(clear_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._chart = CalibrationChart()
        lay.addWidget(self._chart)
        self._calib_note = QLabel("Record at least one genuine and one impostor sample to get a suggestion.")
        self._calib_note.setObjectName("caption")
        self._calib_note.setWordWrap(True)
        lay.addWidget(self._calib_note)
        self._refresh_calibration()
        return card

    def _tag_calibration(self, genuine: bool) -> None:
        if not self._recognition_rows:
            self._log("Nothing to record — run Identify first.")
            return
        sim = float(self._recognition_rows[0]["centroid"])
        self._calibration.append((sim, genuine))
        self._log(f"Calibration: recorded {'genuine' if genuine else 'impostor'} sample at {sim:.3f}.")
        self._refresh_calibration()

    def _clear_calibration(self) -> None:
        self._calibration = []
        self._refresh_calibration()

    def _refresh_calibration(self) -> None:
        threshold = self._thr_slider.value() / 100
        self._chart.set_data(self._calibration, threshold)
        genuine = [s for s, g in self._calibration if g]
        impostor = [s for s, g in self._calibration if not g]
        if genuine and impostor:
            suggested = (min(genuine) + max(impostor)) / 2
            overlap = max(impostor) >= min(genuine)
            text = f"Suggested threshold: {suggested:.3f}"
            if overlap:
                text += " — distributions overlap, no single cutoff perfectly separates these samples yet."
            self._calib_note.setText(text)
            self._calib_note.setStyleSheet(
                f"font-size: {theme.CAPTION_SIZE}px; color: {theme.WARNING if overlap else theme.TEXT_SECONDARY};")
        else:
            self._calib_note.setText("Record at least one genuine and one impostor sample to get a suggestion.")
            self._calib_note.setStyleSheet(f"font-size: {theme.CAPTION_SIZE}px; color: {theme.TEXT_SECONDARY};")

    # -- liveness ----------------------------------------------------------------------------------
    def _liveness_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Liveness cues")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        note = QLabel(
            "Thresholds below are live from the engine's tuning. Per-frame readings need "
            "an active unlock scan, which the Manager doesn't run — cues report \"no signal\" here.")
        note.setObjectName("caption")
        note.setWordWrap(True)
        lay.addWidget(note)

        cues = self.bridge.liveness_snapshot()
        for role, title, caption in [
            ("deny", "Deny — evidence of a spoof",
             "Either one firing fails the scan outright and overrides any confirmation."),
            ("confirm", "Confirm — evidence of a real face",
             "Any one firing passes liveness. None firing is not a failure — the scan just keeps looking."),
        ]:
            group = [c for c in cues if c.role == role]
            if not group:
                continue
            t = QLabel(title)
            t.setStyleSheet(f"font-size: {theme.CAPTION_SIZE}px; font-weight: 600;")
            lay.addWidget(t)
            for cue in group:
                row = QHBoxLayout()
                name = QLabel(cue.title)
                name.setToolTip(cue.explanation)
                name.setMinimumWidth(140)
                row.addWidget(name)
                bar = CueBar()
                bar.set_levels(cue.level, cue.threshold)
                row.addWidget(bar, stretch=1)
                state = QLabel("no signal")
                state.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; font-size: 11px;")
                state.setMinimumWidth(90)
                state.setAlignment(Qt.AlignmentFlag.AlignRight)
                row.addWidget(state)
                lay.addLayout(row)
            cap = QLabel(caption)
            cap.setObjectName("hint")
            cap.setWordWrap(True)
            lay.addWidget(cap)
        return card

    # -- log ------------------------------------------------------------------------------------
    def _log_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Log")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        self._log_view = QTextEdit()
        self._log_view.setObjectName("log")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(140)
        lay.addWidget(self._log_view)
        self._log("Face Lab opened.")
        if not self.bridge.using_real_engine:
            self._log("Simulated engine — live analysis sections are unavailable.")
        return card

    # -- visibility -----------------------------------------------------------------------------------
    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._refresh_identities()
        self._poll.start(4000)
        super().showEvent(event)
