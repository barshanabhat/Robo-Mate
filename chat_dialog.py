from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout
)

from ai_provider import OllamaProvider


class AIResponseWorker(QThread):
    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, provider, message, project_context, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.message = message
        self.project_context = project_context

    def run(self):
        try:
            response = self.provider.ask(
                self.message,
                self.project_context
            )
            self.response_ready.emit(response)
        except Exception:
            self.request_failed.emit(
                "I couldn't reach the AI service. Please try again."
            )


class ChatDialog(QDialog):
    def __init__(self, parent=None, current_project=None, provider=None):
        super().__init__(parent)
        self.current_project = current_project
        self.provider = provider or OllamaProvider()
        self.response_worker = None

        self.setWindowTitle("Chat with RoboMate")
        self.setWindowFlags(
            Qt.Dialog |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.resize(380, 420)

        self.message_area = QPlainTextEdit()
        self.message_area.setReadOnly(True)
        self.message_area.setStyleSheet("""
            QPlainTextEdit {
                background-color: #202a30;
                color: #f0f7f9;
                border: 1px solid #3beaff;
                border-radius: 6px;
                padding: 8px;
            }
        """)

        self.status_label = QLabel("RoboMate is thinking...")
        self.status_label.hide()

        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type a message...")
        self.message_input.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("Send")
        self.send_button.setAutoDefault(False)
        self.send_button.setDefault(False)
        self.send_button.clicked.connect(self.send_message)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.setDefault(False)
        self.close_button.clicked.connect(self.close)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_button)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message_area)
        layout.addWidget(self.status_label)
        layout.addLayout(input_layout)
        layout.addLayout(button_layout)
        self.setStyleSheet("""
            QDialog {
                background-color: #151d22;
                color: #f0f7f9;
            }
            QPushButton {
                background-color: #123c46;
                color: #f0f7f9;
                border: 1px solid #3beaff;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #1a5966;
            }
            QLineEdit {
                background-color: #202a30;
                color: #f0f7f9;
                border: 1px solid #3beaff;
                border-radius: 5px;
                padding: 7px;
            }
        """)

    def send_message(self):
        message = self.message_input.text().strip()
        if not message:
            return

        self.message_area.appendPlainText(f"Me:\n{message}\n")
        self.message_input.clear()
        self.send_button.setEnabled(False)
        self.message_input.setEnabled(False)
        self.status_label.show()

        self.response_worker = AIResponseWorker(
            self.provider,
            message,
            self.current_project,
            self
        )
        self.response_worker.response_ready.connect(self.show_response)
        self.response_worker.request_failed.connect(self.show_error)
        self.response_worker.finished.connect(self.finish_request)
        self.response_worker.finished.connect(self.response_worker.deleteLater)
        self.response_worker.start()

    def show_response(self, response):
        self.message_area.appendPlainText(f"RoboMate:\n{response}\n")

    def show_error(self, message):
        self.message_area.appendPlainText(f"RoboMate:\n{message}\n")

    def finish_request(self):
        self.status_label.hide()
        self.send_button.setEnabled(True)
        self.message_input.setEnabled(True)
        self.message_input.setFocus()
        self.response_worker = None
