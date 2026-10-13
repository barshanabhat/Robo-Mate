import difflib

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
from project_reader import ProjectReader
from terminal_runner import CommandNotAllowedError, SafeTerminalRunner


class SafeCommandWorker(QThread):
    command_finished = Signal(str)

    def __init__(self, project_folder, command, parent=None):
        super().__init__(parent)
        self.project_folder = project_folder
        self.command = command

    def run(self):
        try:
            runner = SafeTerminalRunner(self.project_folder)
            output = runner.run(self.command)
        except FileNotFoundError:
            output = "The project folder or command executable was not found."
        except NotADirectoryError:
            output = "The selected project folder is not a directory."
        except PermissionError as error:
            output = str(error)
        except OSError:
            output = "RoboMate could not run that command."
        self.command_finished.emit(output)


class AIResponseWorker(QThread):
    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(
        self,
        provider,
        message,
        project_context,
        file_analysis=None,
        parent=None
    ):
        super().__init__(parent)
        self.provider = provider
        self.message = message
        self.project_context = project_context
        self.file_analysis = file_analysis

    def run(self):
        try:
            if self.file_analysis is None:
                response = self.provider.ask(
                    self.message,
                    self.project_context
                )
            else:
                command, file_path, file_content, request = self.file_analysis
                if command == "edit":
                    response = self.provider.generate_updated_file(
                        file_path,
                        file_content,
                        request,
                        self.project_context
                    )
                elif command == "propose":
                    response = self.provider.propose_file_change(
                        file_path,
                        file_content,
                        request,
                        self.project_context
                    )
                    response += (
                        "\n\n⚠️ Preview only — no files were modified."
                    )
                else:
                    response = self.provider.analyze_file(
                        file_path,
                        file_content,
                        request,
                        self.project_context
                    )
            self.response_ready.emit(response)
        except Exception:
            self.request_failed.emit(
                "I can't reach Ollama. Make sure Ollama is running."
            )


class ChatDialog(QDialog):
    def __init__(self, parent=None, current_project=None, provider=None):
        super().__init__(parent)
        self.current_project = current_project
        self.provider = provider or OllamaProvider()
        self.response_worker = None
        self.generating_edit = None
        self.pending_edit = None

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

        if self.handle_local_project_command(message):
            return

        if self.handle_terminal_command(message):
            return

        if self.handle_edit_control_command(message):
            return

        file_edit = self.prepare_file_edit(message)
        if file_edit is False:
            return
        if file_edit is not None:
            self.generating_edit = file_edit
            self.start_ai_request(message, file_edit["worker_data"])
            return

        file_analysis = self.prepare_file_analysis(message)
        if file_analysis is False:
            return

        self.start_ai_request(message, file_analysis)

    def start_ai_request(self, message, file_analysis=None):
        self.send_button.setEnabled(False)
        self.message_input.setEnabled(False)
        self.status_label.show()

        self.response_worker = AIResponseWorker(
            self.provider,
            message,
            self.current_project,
            file_analysis,
            self
        )
        self.response_worker.response_ready.connect(self.handle_ai_response)
        self.response_worker.request_failed.connect(self.show_error)
        self.response_worker.finished.connect(self.finish_request)
        self.response_worker.finished.connect(self.response_worker.deleteLater)
        self.response_worker.start()

    def handle_terminal_command(self, message):
        if message != "/run" and not message.startswith("/run "):
            return False

        command = message[len("/run"):].strip()
        if not command:
            self.show_response(
                "Usage: /run command\n"
                "Allowed commands include: python --version, "
                "python -m pytest, pytest, npm test, npm run test, "
                "npm run build, npm run lint, git status, git diff, "
                "git log --oneline -5"
            )
            return True

        try:
            SafeTerminalRunner.validate_command(command)
        except CommandNotAllowedError as error:
            self.show_response(f"Command not allowed: {error}")
            return True

        if self.current_project is None:
            self.show_response("Select a project with a valid folder first.")
            return True

        project_folder = self.current_project[2]
        if not project_folder:
            self.show_response("The selected project has no folder path.")
            return True

        self.send_button.setEnabled(False)
        self.message_input.setEnabled(False)
        self.status_label.setText("RoboMate is running the command...")
        self.status_label.show()
        self.response_worker = SafeCommandWorker(
            project_folder,
            command,
            self
        )
        self.response_worker.command_finished.connect(
            lambda output: self.show_response(
                f"Command output:\n{output}"
            )
        )
        self.response_worker.finished.connect(self.finish_request)
        self.response_worker.finished.connect(self.response_worker.deleteLater)
        self.response_worker.start()
        return True

    def prepare_file_edit(self, message):
        if message != "/edit" and not message.startswith("/edit "):
            return None

        if self.pending_edit is not None or self.generating_edit is not None:
            self.show_response(
                "There is already a pending edit. Type /approve or /cancel "
                "before requesting another."
            )
            return False

        command_text = message[len("/edit"):].strip()
        if "|" not in command_text:
            self.show_response(
                "Usage: /edit relative/path/to/file | requested change"
            )
            return False

        relative_path, request = command_text.split("|", 1)
        relative_path = relative_path.strip()
        request = request.strip()
        if not relative_path or not request:
            self.show_response(
                "Usage: /edit relative/path/to/file | requested change"
            )
            return False
        if self.current_project is None:
            self.show_response("Select a project with a valid folder first.")
            return False

        project_folder = self.current_project[2]
        if not project_folder:
            self.show_response("The selected project has no folder path.")
            return False

        try:
            reader = ProjectReader(project_folder)
            original_content = reader.read_file(relative_path)
        except (FileNotFoundError, NotADirectoryError):
            self.show_response(
                "The project folder or requested file was not found."
            )
            return False
        except PermissionError:
            self.show_response(
                "RoboMate does not have permission to read that file."
            )
            return False
        except ValueError as error:
            self.show_response(f"Could not read that file: {error}")
            return False
        except OSError:
            self.show_response(
                "RoboMate could not access that project file."
            )
            return False

        return {
            "file_path": relative_path,
            "request": request,
            "original_content": original_content,
            "project_folder": project_folder,
            "worker_data": (
                "edit",
                relative_path,
                original_content,
                request
            )
        }

    def handle_edit_control_command(self, message):
        if message == "/approve":
            self.approve_pending_edit()
            return True
        if message == "/cancel":
            if self.pending_edit is None:
                self.show_response("No pending change to cancel.")
            else:
                self.pending_edit = None
                self.show_response(
                    "Pending change cancelled. No files were modified."
                )
            return True
        if message == "/undo":
            self.undo_last_edit()
            return True
        return False

    def handle_ai_response(self, response):
        if self.generating_edit is None:
            self.show_response(response)
            return

        edit = self.generating_edit
        self.generating_edit = None
        updated_content = response
        validation_text = updated_content.strip()
        error_prefixes = (
            "i can't reach ollama.",
            "i couldn't get a response from ollama.",
            "the ollama model ",
            "ollama returned an invalid response.",
            "error:",
            "unable to "
        )
        explanation_prefixes = (
            "here is",
            "here's",
            "the updated file",
            "updated file:",
            "the change",
            "change the",
            "the code",
            "i have",
            "i've",
            "i cannot",
            "i can't",
            "i am unable",
            "sure",
            "this is",
            "explanation:",
            "the requested",
            "to address",
            "based on",
            "because"
        )
        first_line = (
            validation_text.splitlines()[0].strip().lower()
            if validation_text
            else ""
        )
        if (
            not validation_text
            or "```" in validation_text
            or validation_text.lower().startswith(error_prefixes)
            or first_line.startswith(explanation_prefixes)
            or len(updated_content.encode("utf-8")) > ProjectReader.MAX_FILE_SIZE
        ):
            self.show_response(
                "I couldn't prepare a safe full-file update. "
                "The response was empty, explanatory, an error, or exceeded "
                "the 200 KB limit. "
                "No files were modified. Please try /edit again."
            )
            return

        proposed_diff = "\n".join(
            difflib.unified_diff(
                edit["original_content"].splitlines(),
                updated_content.splitlines(),
                fromfile=f"a/{edit['file_path']}",
                tofile=f"b/{edit['file_path']}",
                lineterm=""
            )
        )
        self.pending_edit = {
            "file_path": edit["file_path"],
            "request": edit["request"],
            "original_content": edit["original_content"],
            "new_content": updated_content,
            "project_folder": edit["project_folder"]
        }
        self.show_response(
            f"Edit preview for: {edit['file_path']}\n"
            f"Requested change: {edit['request']}\n"
            "Summary: A complete-file update was generated for this request.\n"
            "No files are changed until you approve the update.\n\n"
            f"Proposed diff:\n{proposed_diff or 'No text changes were generated.'}\n\n"
            "Type /approve to apply this change\n"
            "Type /cancel to discard it"
        )

    def approve_pending_edit(self):
        if self.pending_edit is None:
            self.show_response("No pending change to approve.")
            return

        edit = self.pending_edit
        try:
            reader = ProjectReader(edit["project_folder"])
            reader.write_file(
                edit["file_path"],
                edit["new_content"],
                expected_content=edit["original_content"]
            )
        except (FileNotFoundError, NotADirectoryError):
            self.show_response(
                "The project folder or requested file was not found. "
                "No change was applied."
            )
            return
        except PermissionError:
            self.show_response(
                "RoboMate does not have permission to update that file."
            )
            return
        except ValueError as error:
            self.show_response(f"Could not apply the change: {error}")
            return
        except OSError:
            self.show_response(
                "RoboMate could not safely update that file. "
                "Check the project folder and try again."
            )
            return

        self.pending_edit = None
        self.show_response(
            f"✅ {edit['file_path']} updated successfully."
        )

    def undo_last_edit(self):
        self.show_response(
            "Undo is not available yet. Git-based rollback will be added later."
        )

    def handle_local_project_command(self, message):
        if message == "/files":
            command = "files"
        elif message == "/read" or message.startswith("/read "):
            command = "read"
        else:
            return False

        if self.current_project is None:
            self.show_response("Select a project with a valid folder first.")
            return True

        project_folder = self.current_project[2]
        if not project_folder:
            self.show_response("The selected project has no folder path.")
            return True

        try:
            reader = ProjectReader(project_folder)
            if command == "files":
                files = reader.list_files()
                if files:
                    result = "Project files:\n\n" + "\n".join(files)
                else:
                    result = "No readable project files were found."
            else:
                relative_path = message[len("/read"):].strip()
                if not relative_path:
                    result = "Usage: /read relative/path/to/file"
                else:
                    content = reader.read_file(relative_path)
                    result = f"File: {relative_path}\n\n{content}"
            self.show_response(result)
        except FileNotFoundError:
            self.show_response("The project folder or requested file was not found.")
        except NotADirectoryError:
            self.show_response("The selected project folder is not a directory.")
        except PermissionError:
            self.show_response("RoboMate does not have permission to read that file.")
        except ValueError as error:
            self.show_response(f"Could not read that file: {error}")
        except OSError:
            self.show_response("RoboMate could not access that project file.")

        return True

    def prepare_file_analysis(self, message):
        if message == "/analyze" or message.startswith("/analyze "):
            command = "analyze"
            command_text = message[len("/analyze"):].strip()
            request = None
            relative_path = command_text
        elif message == "/askfile" or message.startswith("/askfile "):
            command = "askfile"
            command_text = message[len("/askfile"):].strip()
            if "|" not in command_text:
                self.show_response(
                    "Usage: /askfile relative/path/to/file | question"
                )
                return False

            relative_path, request = command_text.split("|", 1)
            relative_path = relative_path.strip()
            request = request.strip()
            if not request:
                self.show_response("Please include a question after the |.")
                return False
        elif message == "/propose" or message.startswith("/propose "):
            command = "propose"
            command_text = message[len("/propose"):].strip()
            if "|" not in command_text:
                self.show_response(
                    "Usage: /propose relative/path/to/file | requested change"
                )
                return False

            relative_path, request = command_text.split("|", 1)
            relative_path = relative_path.strip()
            request = request.strip()
            if not request:
                self.show_response(
                    "Please describe the change you want after the |."
                )
                return False
        else:
            return None

        if not relative_path:
            usage = (
                "Usage: /analyze relative/path/to/file"
                if command == "analyze"
                else (
                    "Usage: /propose relative/path/to/file | requested change"
                    if command == "propose"
                    else "Usage: /askfile relative/path/to/file | question"
                )
            )
            self.show_response(usage)
            return False

        if self.current_project is None:
            self.show_response("Select a project with a valid folder first.")
            return False

        project_folder = self.current_project[2]
        if not project_folder:
            self.show_response("The selected project has no folder path.")
            return False

        try:
            reader = ProjectReader(project_folder)
            file_content = reader.read_file(relative_path)
        except FileNotFoundError:
            self.show_response("The project folder or requested file was not found.")
            return False
        except NotADirectoryError:
            self.show_response("The selected project folder is not a directory.")
            return False
        except PermissionError:
            self.show_response("RoboMate does not have permission to read that file.")
            return False
        except ValueError as error:
            self.show_response(f"Could not read that file: {error}")
            return False
        except OSError:
            self.show_response("RoboMate could not access that project file.")
            return False

        return command, relative_path, file_content, request

    def show_response(self, response):
        self.message_area.appendPlainText(f"RoboMate:\n{response}\n")

    def show_error(self, message):
        if self.generating_edit is not None:
            self.generating_edit = None
        self.message_area.appendPlainText(f"RoboMate:\n{message}\n")

    def finish_request(self):
        self.status_label.hide()
        self.status_label.setText("RoboMate is thinking...")
        self.send_button.setEnabled(True)
        self.message_input.setEnabled(True)
        self.message_input.setFocus()
        self.response_worker = None
