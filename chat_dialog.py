from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout
)


class ChatDialog(QDialog):
    def __init__(self, parent=None, current_project=None):
        super().__init__(parent)
        self.current_project = current_project

        self.setWindowTitle("Chat with RoboMate")
        self.setWindowFlags(
            Qt.Dialog |
            Qt.WindowStaysOnTopHint
        )
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

        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type a message...")
        self.message_input.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send_message)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_button)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message_area)
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

        if self.current_project is not None:
            project_name = self.current_project[1]
            response = (
                f"I heard you. Current project: {project_name}.\n"
                "My AI brain is coming next! 🤖"
            )
        else:
            response = "I heard you. My AI brain is coming next! 🤖"

        self.message_area.appendPlainText(f"RoboMate:\n{response}\n")
