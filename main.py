import sys

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget


class RoboMateWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.drag_position = QPoint()
        self.eyes_closed = False
        self.expression = "normal"

        # Idle animation values
        self.float_offset = 0
        self.float_direction = 1

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )

        self.setAttribute(Qt.WA_TranslucentBackground)

        self.resize(260, 300)

        # Message bubble
        self.message_label = QLabel("Hi! I'm RoboMate 👋", self)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setGeometry(10, 5, 240, 45)

        self.message_label.setStyleSheet("""
            QLabel {
                background-color: white;
                color: black;
                border: 2px solid #00dfff;
                border-radius: 15px;
                padding: 6px;
                font-size: 13px;
            }
        """)

        # Close button
        self.close_button = QPushButton("Close", self)
        self.close_button.setGeometry(80, 250, 100, 32)
        self.close_button.clicked.connect(QApplication.instance().quit)

        # Blink timer
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.start_blink)
        self.blink_timer.start(3000)

        # Idle floating animation timer
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.update_idle_animation)
        self.idle_timer.start(60)

    def update_idle_animation(self):
        self.float_offset += self.float_direction

        if self.float_offset >= 5:
            self.float_direction = -1

        elif self.float_offset <= -5:
            self.float_direction = 1

        self.update()

    def start_blink(self):
        if self.expression != "normal":
            return

        self.eyes_closed = True
        self.update()

        QTimer.singleShot(180, self.end_blink)

    def end_blink(self):
        self.eyes_closed = False
        self.update()

    def show_happy(self):
        self.expression = "happy"

        self.message_label.setText(
            "Yay! Let's build something! 🤖"
        )

        self.update()

        QTimer.singleShot(
            1500,
            self.reset_expression
        )

    def reset_expression(self):
        self.expression = "normal"

        self.message_label.setText(
            "Hi! I'm RoboMate 👋"
        )

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        painter.translate(130, 137)
        painter.scale(0.65, 0.65)
        painter.translate(-160, -177)

        y = self.float_offset

        outline = QPen(QColor("#b8d2dc"), 2)
        shell = QLinearGradient(0, 190 + y, 0, 290 + y)
        shell.setColorAt(0, QColor("#ffffff"))
        shell.setColorAt(1, QColor("#dcebf0"))

        # Short, chunky legs and oversized rounded feet.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(131, 246 + y, 21, 28, 10, 10)
        painter.drawRoundedRect(168, 246 + y, 21, 28, 10, 10)
        painter.drawEllipse(112, 265 + y, 51, 27)
        painter.drawEllipse(157, 265 + y, 51, 27)

        # Chubby little arms with round shoulders and small hands.
        painter.drawRoundedRect(91, 207 + y, 34, 23, 11, 11)
        painter.drawRoundedRect(195, 207 + y, 34, 23, 11, 11)
        painter.drawEllipse(99, 198 + y, 32, 32)
        painter.drawEllipse(189, 198 + y, 32, 32)
        painter.drawEllipse(80, 209 + y, 23, 23)
        painter.drawEllipse(217, 209 + y, 23, 23)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#a6eaf5"))
        painter.drawEllipse(87, 215 + y, 8, 8)
        painter.drawEllipse(225, 215 + y, 8, 8)

        # Scale the complete head, visor, and face together by 88%.
        painter.save()
        painter.translate(160, 134 + y)
        painter.scale(0.88, 0.88)
        painter.translate(-160, -134 - y)

        # Small round ear panels peek out around the oversized head.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawEllipse(42, 111 + y, 48, 48)
        painter.drawEllipse(230, 111 + y, 48, 48)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#8beaff"))
        painter.drawEllipse(54, 123 + y, 24, 24)
        painter.drawEllipse(242, 123 + y, 24, 24)
        painter.setBrush(QColor("#d9f9ff"))
        painter.drawEllipse(60, 127 + y, 8, 8)
        painter.drawEllipse(248, 127 + y, 8, 8)

        # The large rounded head is the main feature of the silhouette.
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(58, 65 + y, 204, 137, 60, 60)
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawArc(77, 72 + y, 166, 94, 35 * 16, 105 * 16)

        # Smooth dark visor, inset into the head.
        visor = QLinearGradient(0, 87 + y, 0, 180 + y)
        visor.setColorAt(0, QColor("#182a33"))
        visor.setColorAt(1, QColor("#05090c"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(visor)
        painter.drawRoundedRect(77, 88 + y, 166, 87, 43, 43)

        # Bright cyan eyes keep their normal, blinking, and happy states.
        painter.setBrush(QColor("#3beaff"))
        if self.expression == "happy":
            painter.setPen(QPen(QColor("#3beaff"), 5))
            painter.drawArc(111, 121 + y, 31, 22, 0, 180 * 16)
            painter.drawArc(178, 121 + y, 31, 22, 0, 180 * 16)
        elif self.eyes_closed:
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(118, 132 + y, 19, 4, 2, 2)
            painter.drawRoundedRect(185, 132 + y, 19, 4, 2, 2)
        else:
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(118, 116 + y, 19, 27)
            painter.drawEllipse(185, 116 + y, 19, 27)

        # A small cyan arc makes a friendly smile.
        painter.setPen(QPen(QColor("#8beaff"), 3))
        painter.drawArc(151, 146 + y, 19, 11, 200 * 16, 140 * 16)
        painter.restore()

        # A round, chubby torso with a soft inset belly panel.
        belly = QLinearGradient(0, 204 + y, 0, 261 + y)
        belly.setColorAt(0, QColor("#f8feff"))
        belly.setColorAt(1, QColor("#e3f5f8"))
        painter.setPen(outline)
        painter.setBrush(shell)
        painter.drawRoundedRect(107, 197 + y, 106, 68, 34, 34)

        painter.setPen(Qt.NoPen)
        painter.setBrush(belly)
        painter.drawEllipse(117, 204 + y, 86, 55)

        # Small circular chest badge sits on the soft belly panel.
        painter.setPen(QPen(QColor("#a6eaf5"), 2))
        painter.setBrush(QColor("#d9f9ff"))
        painter.drawEllipse(148, 216 + y, 24, 24)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3beaff"))
        painter.drawEllipse(155, 223 + y, 10, 10)
        painter.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:

            click_position = event.position()

            if (
                95 <= click_position.x() <= 165
                and
                146 <= click_position.y() <= 198
            ):
                self.show_happy()

            self.drag_position = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:

            self.move(
                event.globalPosition().toPoint()
                - self.drag_position
            )


app = QApplication(sys.argv)

window = RoboMateWindow()
window.show()

sys.exit(app.exec())