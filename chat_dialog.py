import ast
import difflib
import re
import textwrap
import sqlite3

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

    def __init__(
        self,
        project_folder,
        command=None,
        operation="run",
        verification_passed=False,
        project_context=None,
        parent=None
    ):
        super().__init__(parent)
        self.project_folder = project_folder
        self.command = command
        self.operation = operation
        self.verification_passed = verification_passed
        self.project_context = project_context

    def run(self):
        try:
            runner = SafeTerminalRunner(self.project_folder)
            if self.operation == "checkpoint":
                output = runner.checkpoint(self.command)
            elif self.operation == "git_status":
                output = runner.git_status()
            elif self.operation == "push":
                output = runner.push()
            elif self.operation == "test":
                output = runner.run_tests()
            elif self.operation == "verify":
                output = runner.verify()
            elif self.operation == "workflow":
                output = runner.workflow_status(self.verification_passed)
            elif self.operation == "summary":
                output = self._project_summary(runner)
            else:
                output = runner.run(self.command)
        except FileNotFoundError:
            output = "The project folder or command executable was not found."
        except NotADirectoryError:
            output = "The selected project folder is not a directory."
        except PermissionError as error:
            output = str(error)
        except TimeoutError as error:
            output = str(error)
        except (RuntimeError, ValueError) as error:
            output = str(error)
        except OSError:
            output = "RoboMate could not run that command."
        self.command_finished.emit(output)

    def _project_summary(self, runner):
        project = self.project_context
        project_name = self._project_field(project, 1)
        project_folder = self._project_field(project, 2)
        git = runner.git_summary()

        try:
            if not project_folder:
                raise ValueError("The project folder is not available.")
            reader = ProjectReader(project_folder)
            relevant_file_count = len(reader.list_files())
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
            relevant_file_count = None

        has_changes = git["changes"] == "Yes"
        verified = (
            has_changes
            and self.verification_passed
        )

        lines = [
            "Project Summary",
            "",
            f"Name: {project_name}",
            f"Folder: {project_folder or 'Not available'}",
            f"Description: {self._project_field(project, 3)}",
            f"Technologies: {self._project_field(project, 4)}",
            "",
            "Git:",
            f"Repository: {git['repository']}",
            f"Branch: {git['branch']}",
            f"Upstream: {git['upstream']}",
            f"Changes: {git['changes']}",
            f"Ahead: {git['ahead']}",
            f"Behind: {git['behind']}",
            f"Latest commit: {git['latest_commit']}",
            "",
            "Files:",
            (
                f"Relevant files: {relevant_file_count}"
                if relevant_file_count is not None
                else "Relevant files: Not available"
            ),
            "",
            "Verification:",
            "Verified: Yes" if verified else "Not verified for current working state",
            "",
            f"Current task: {self._project_field(project, 5)}",
            f"Next action: {self._project_field(project, 6)}"
        ]
        return runner._limit_output("\n".join(lines))

    @staticmethod
    def _project_field(project, index):
        if project is None or len(project) <= index:
            return "Not available"
        return project[index] or "Not available"


class AIResponseWorker(QThread):
    response_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(
        self,
        provider,
        message,
        project_context,
        file_analysis=None,
        parent=None,
        code_context=None
    ):
        super().__init__(parent)
        self.provider = provider
        self.message = message
        self.project_context = project_context
        self.file_analysis = file_analysis
        self.code_context = code_context

    def run(self):
        try:
            if self.file_analysis is None:
                if self.code_context:
                    response = self.provider.ask(
                        self.message,
                        self.project_context,
                        code_context=self.code_context
                    )
                else:
                    response = self.provider.ask(
                        self.message,
                        self.project_context
                    )
            else:
                command, file_path, file_content, request = self.file_analysis
                if command in ("edit", "fix"):
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
        self.database = getattr(parent, "database", None)
        self.provider = provider or OllamaProvider()
        self.response_worker = None
        self.generating_edit = None
        self.pending_edit = None
        self.last_verification_passed = False
        self.verified_project_folder = None
        self.last_code_context = None

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

        if message == "/understand" or message.startswith("/understand "):
            print("UNDERSTAND COMMAND DETECTED", flush=True)

        if (
            self.parent() is not None
            and hasattr(self.parent(), "current_project")
        ):
            previous_project = self._project_identity(self.current_project)
            self.current_project = self.parent().current_project
            if previous_project != self._project_identity(self.current_project):
                self.last_code_context = None

        self.message_area.appendPlainText(f"Me:\n{message}\n")
        self.message_input.clear()

        if self.handle_local_project_command(message):
            return

        if self.handle_project_memory_command(message):
            return

        if self.handle_terminal_command(message):
            return

        if self.handle_edit_control_command(message):
            return

        file_edit = self.prepare_file_edit(message)
        if file_edit is None:
            file_edit = self.prepare_file_fix(message)
        if file_edit is False:
            return
        if file_edit is not None:
            self.generating_edit = file_edit
            self.start_ai_request(message, file_edit["worker_data"])
            return

        file_analysis = self.prepare_file_analysis(message)
        if file_analysis is False:
            return

        code_context = (
            self.last_code_context
            if file_analysis is None
            else None
        )
        if code_context:
            self.show_response("Using gathered project context...")
        self.start_ai_request(message, file_analysis, code_context)

    def handle_project_memory_command(self, message):
        if message == "/task" or message.startswith("/task "):
            field = "task"
            command = "/task"
        elif message == "/next" or message.startswith("/next "):
            field = "next"
            command = "/next"
        else:
            return False

        value = message[len(command):].strip()
        if not value:
            self.show_response(f"Usage: {command} <text> or {command} clear")
            return True
        if len(value) > 500:
            self.show_response("Task and next-action text must be 500 characters or fewer.")
            return True

        project = self.current_project
        if project is None:
            self.show_response("Select a project first.")
            return True
        if self.database is None:
            self.show_response("Project memory is not available right now.")
            return True

        stored_value = "" if value.casefold() == "clear" else value
        try:
            if field == "task":
                self.database.update_project_task(project[0], stored_value)
            else:
                self.database.update_project_next_action(
                    project[0],
                    stored_value
                )
            self.current_project = self.database.get_project_by_id(project[0])
            if (
                self.parent() is not None
                and hasattr(self.parent(), "current_project")
            ):
                self.parent().current_project = self.current_project
        except LookupError as error:
            self.show_response(str(error))
            return True
        except (sqlite3.Error, OSError, ValueError):
            self.show_response("RoboMate could not update this project's memory.")
            return True

        if self.current_project is None:
            self.show_response("The selected project could not be reloaded.")
            return True

        label = "Current task" if field == "task" else "Next action"
        if stored_value:
            response = f"{label} updated:\n{stored_value}"
        else:
            response = f"{label} cleared."
        self.show_response(response)
        return True

    def start_ai_request(self, message, file_analysis=None, code_context=None):
        self.send_button.setEnabled(False)
        self.message_input.setEnabled(False)
        self.status_label.show()

        self.response_worker = AIResponseWorker(
            self.provider,
            message,
            self.current_project,
            file_analysis,
            self,
            code_context
        )
        self.response_worker.response_ready.connect(self.handle_ai_response)
        self.response_worker.request_failed.connect(self.show_error)
        self.response_worker.finished.connect(self.finish_request)
        self.response_worker.finished.connect(self.response_worker.deleteLater)
        self.response_worker.start()

    def handle_terminal_command(self, message):
        is_summary = (
            message == "/summary"
            or message.startswith("/summary ")
        )
        is_workflow = (
            message == "/workflow"
            or message.startswith("/workflow ")
        )
        is_verify = message == "/verify" or message.startswith("/verify ")
        is_test = message == "/test" or message.startswith("/test ")
        is_run = message == "/run" or message.startswith("/run ")
        is_git_status = (
            message == "/gitstatus"
            or message.startswith("/gitstatus ")
        )
        is_checkpoint = (
            message == "/checkpoint"
            or message.startswith("/checkpoint ")
        )
        is_push = message == "/push" or message.startswith("/push ")
        if (
            not is_run
            and not is_summary
            and not is_workflow
            and not is_verify
            and not is_test
            and not is_git_status
            and not is_checkpoint
            and not is_push
        ):
            return False

        if is_summary:
            if message != "/summary":
                self.show_response("Usage: /summary")
                return True
            operation = "summary"
            command = None
        elif is_workflow:
            if message != "/workflow":
                self.show_response("Usage: /workflow")
                return True
            operation = "workflow"
            command = None
        elif is_verify:
            if message != "/verify":
                self.show_response("Usage: /verify")
                return True
            operation = "verify"
            command = None
        elif is_test:
            if message != "/test":
                self.show_response("Usage: /test")
                return True
            operation = "test"
            command = None
        elif is_run:
            operation = "run"
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
        elif is_git_status:
            if message != "/gitstatus":
                self.show_response("Usage: /gitstatus")
                return True
            operation = "git_status"
            command = None
        elif is_checkpoint:
            operation = "checkpoint"
            command = message[len("/checkpoint"):].strip()
            try:
                command = SafeTerminalRunner.validate_commit_message(command)
            except ValueError as error:
                self.show_response(f"Checkpoint not allowed: {error}")
                return True
        elif is_push:
            if message != "/push":
                self.show_response("Usage: /push")
                return True
            operation = "push"
            command = None

        if self.current_project is None:
            self.show_response(
                "Select a project with a valid folder first."
            )
            return True

        project_folder = self.current_project[2]
        if not project_folder:
            self.show_response("The selected project has no folder path.")
            return True

        project_folder_key = str(project_folder).casefold()
        if operation == "verify":
            self.last_verification_passed = False
            self.verified_project_folder = project_folder_key
        elif operation in ("run", "test", "checkpoint", "push"):
            self.invalidate_verification()
        verification_passed = (
            self.last_verification_passed
            and self.verified_project_folder == project_folder_key
        )

        self.send_button.setEnabled(False)
        self.message_input.setEnabled(False)
        self.status_label.setText("RoboMate is running the command...")
        self.status_label.show()
        self.response_worker = SafeCommandWorker(
            project_folder,
            command,
            operation=operation,
            verification_passed=verification_passed,
            project_context=self.current_project,
            parent=self
        )
        if operation == "run":
            self.response_worker.command_finished.connect(
                lambda output: self.show_response(
                    f"Command output:\n{output}"
                )
            )
        elif operation == "verify":
            self.response_worker.command_finished.connect(
                lambda output: self.handle_verification_response(
                    output,
                    project_folder_key
                )
            )
        else:
            self.response_worker.command_finished.connect(self.show_response)
        self.response_worker.finished.connect(self.finish_request)
        self.response_worker.finished.connect(self.response_worker.deleteLater)
        self.response_worker.start()
        return True

    def handle_verification_response(self, output, project_folder_key):
        self.last_verification_passed = (
            "✅ Verification passed." in output
        )
        self.verified_project_folder = (
            project_folder_key if self.last_verification_passed else None
        )
        self.show_response(output)

    def prepare_file_edit(self, message):
        return self.prepare_file_change(message, "edit")

    def prepare_file_fix(self, message):
        return self.prepare_file_change(message, "fix")

    def prepare_file_change(self, message, command):
        command_prefix = f"/{command}"
        if message != command_prefix and not message.startswith(
            f"{command_prefix} "
        ):
            return None

        self.invalidate_verification()
        if self.pending_edit is not None or self.generating_edit is not None:
            self.show_response(
                "There is already a pending change. Type /approve or /cancel "
                "before requesting another."
            )
            return False

        command_text = message[len(command_prefix):].strip()
        if "|" not in command_text:
            request_name = "issue description" if command == "fix" else "requested change"
            self.show_response(
                f"Usage: {command_prefix} relative/path/to/file | "
                f"{request_name}"
            )
            return False

        relative_path, request = command_text.split("|", 1)
        relative_path = relative_path.strip()
        request = request.strip()
        if not relative_path or not request:
            self.show_response(
                f"Usage: {command_prefix} relative/path/to/file | "
                f"{'issue description' if command == 'fix' else 'requested change'}"
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
            "kind": command,
            "original_content": original_content,
            "project_folder": project_folder,
            "worker_data": (
                command,
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
                "the 200 KB limit. No files were modified. Please try "
                f"/{edit['kind']} again."
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
            "kind": edit["kind"],
            "original_content": edit["original_content"],
            "new_content": updated_content,
            "project_folder": edit["project_folder"]
        }
        if edit["kind"] == "fix":
            preview = (
                f"Fix prepared for {edit['file_path']}.\n"
                f"Issue: {edit['request']}\n"
                "\n"
                f"Proposed diff:\n{proposed_diff or 'No text changes were generated.'}\n\n"
                "Type /approve to apply this fix\n"
                "Type /cancel to discard it"
            )
        else:
            preview = (
                f"Edit preview for: {edit['file_path']}\n"
                f"Requested change: {edit['request']}\n"
                "Summary: A complete-file update was generated for this request.\n"
                "No files are changed until you approve the update.\n\n"
                f"Proposed diff:\n{proposed_diff or 'No text changes were generated.'}\n\n"
                "Type /approve to apply this change\n"
                "Type /cancel to discard it"
            )
        self.show_response(preview)

    def approve_pending_edit(self):
        if self.pending_edit is None:
            self.show_response("No pending change to approve.")
            return

        edit = self.pending_edit
        self.invalidate_verification()
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
        self.last_code_context = None
        self.show_response(
            f"✅ {edit['file_path']} updated successfully."
        )

    def invalidate_verification(self):
        self.last_verification_passed = False
        self.verified_project_folder = None

    def undo_last_edit(self):
        self.show_response(
            "Undo is not available yet. Git-based rollback will be added later."
        )

    def handle_local_project_command(self, message):
        if message == "/clearcontext":
            self.last_code_context = None
            self.show_response("Code context cleared.")
            return True
        if message == "/files":
            command = "files"
        elif message == "/read" or message.startswith("/read "):
            command = "read"
        elif message == "/search" or message.startswith("/search "):
            command = "search"
        elif message == "/findcode" or message.startswith("/findcode "):
            command = "findcode"
        elif message == "/context" or message.startswith("/context "):
            command = "context"
        elif message == "/multicontext" or message.startswith("/multicontext "):
            command = "multicontext"
        else:
            return False

        command_text = message[len(f"/{command}"):].strip()
        if command in ("context", "multicontext"):
            self.last_code_context = None
        if command in ("search", "findcode") and not command_text:
            self.show_response(f"Usage: /{command} <query>")
            return True
        if command == "context" and not command_text:
            self.show_response("Usage: /context <question>")
            return True
        if command == "multicontext" and not command_text:
            self.show_response("Usage: /multicontext <question>")
            return True

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
            elif command == "read":
                relative_path = message[len("/read"):].strip()
                if not relative_path:
                    result = "Usage: /read relative/path/to/file"
                else:
                    content = reader.read_file(relative_path)
                    result = f"File: {relative_path}\n\n{content}"
            elif command == "search":
                results = reader.search_files(command_text)
                if not results:
                    result = f'No matches found for "{command_text}".'
                else:
                    result = f'Search results for "{command_text}":\n'
                    for index, item in enumerate(results, start=1):
                        result += (
                            f"\n{index}. {item['path']}\n"
                            f"   matches: {', '.join(item['matches'])}"
                        )
            elif command == "findcode":
                keywords = self._findcode_keywords(command_text)
                if not keywords:
                    result = (
                        "I couldn't find useful search words in that question. "
                        "Try /search <keyword>."
                    )
                else:
                    result = self._format_findcode_results(reader, keywords)
            elif command == "context":
                keywords = self._findcode_keywords(command_text)
                if not keywords:
                    result = (
                        "I couldn't find useful search words in that question. "
                        "Try /context <question> with specific project terms."
                    )
                else:
                    self.last_code_context = self._gather_code_context(
                        reader,
                        keywords
                    )
                    result = self.last_code_context
            else:
                keywords = self._findcode_keywords(command_text)
                if not keywords:
                    result = (
                        "I couldn't find useful search words in that question. "
                        "Try /multicontext <question> with specific project terms."
                    )
                else:
                    self.last_code_context, result = (
                        self._gather_multi_file_context(reader, keywords)
                    )
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

    @staticmethod
    def _findcode_keywords(question):
        stop_words = {
            "a", "about", "an", "and", "are", "code", "does", "find",
            "for", "how", "implemented", "in", "is", "it", "of", "the",
            "this", "that", "to", "where", "what", "which", "with",
            "work", "works"
        }
        keywords = []
        for word in re.findall(r"[A-Za-z0-9_]+", question.casefold()):
            normalized = ChatDialog._normalize_search_word(word)
            if (
                len(normalized) < 3
                or word in stop_words
                or normalized in stop_words
                or normalized in keywords
            ):
                continue
            keywords.append(normalized)
            if len(keywords) == 6:
                break
        return keywords

    @staticmethod
    def _normalize_search_word(word):
        word = word.casefold()
        if len(word) > 5 and word.endswith("ies"):
            return word[:-3] + "y"
        if len(word) > 5 and word.endswith("ing"):
            word = word[:-3]
            if len(word) > 2 and word[-1] == word[-2]:
                word = word[:-1]
            return word
        if len(word) > 4 and word.endswith("ed"):
            word = word[:-2]
            if len(word) > 2 and word[-1] == word[-2]:
                word = word[:-1]
            return word
        if len(word) > 4 and word.endswith("s"):
            return word[:-1]
        return word

    @staticmethod
    def _identifier_words(identifier):
        parts = re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
            identifier
        )
        if not parts:
            parts = [identifier]
        return {
            ChatDialog._normalize_search_word(part)
            for part in parts
            if part
        }

    @staticmethod
    def _format_findcode_results(reader, keywords):
        sorted_results = ChatDialog._rank_findcode_results(reader, keywords)
        if not sorted_results:
            return "No likely relevant files were found."

        lines = ["Likely relevant files:"]
        for relative_path, details in sorted_results[:10]:
            lines.append(f"\n{relative_path}")
            if details["matches"]:
                lines.append(f"- {', '.join(details['matches'][:5])}")
        lines.append("\nYou can inspect one with:\n/analyze <relative/path/to/file>")
        return "\n".join(lines)

    @staticmethod
    def _rank_findcode_results(reader, keywords):
        ranked_results = {}
        file_paths = reader.list_files()
        max_search_results = max(len(file_paths), 1)
        for keyword in keywords:
            for item in reader.search_files(
                keyword,
                max_results=max_search_results
            ):
                entry = ranked_results.setdefault(
                    item["path"],
                    {"matches": [], "keywords": []}
                )
                if keyword not in entry["keywords"]:
                    entry["keywords"].append(keyword)
                for match in item["matches"]:
                    if match not in entry["matches"]:
                        entry["matches"].append(match)

        for relative_path, details in ranked_results.items():
            try:
                content = reader.read_file(relative_path)
            except (OSError, ValueError):
                details["score"] = 0
                continue

            keywords_set = set(details["keywords"])
            path_words = ChatDialog._identifier_words(
                relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            )
            score = 35 * len(keywords_set & path_words)

            function_words = set()
            class_words = set()
            variable_words = set()
            if relative_path.lower().endswith(".py"):
                functions = ProjectReader.extract_python_functions(content)
                if functions is not None:
                    for function in functions:
                        name_words = ChatDialog._identifier_words(
                            function["name"]
                        )
                        matched = keywords_set & name_words
                        if matched:
                            score += 100 * len(matched)
                            function_words.update(matched)

                    try:
                        tree = ast.parse(content)
                    except SyntaxError:
                        tree = None
                    if tree is not None:
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                class_words.update(
                                    keywords_set
                                    & ChatDialog._identifier_words(node.name)
                                )
                            elif isinstance(node, ast.Name) and isinstance(
                                node.ctx,
                                (ast.Store, ast.Del)
                            ):
                                variable_words.update(
                                    keywords_set
                                    & ChatDialog._identifier_words(node.id)
                                )
                            elif isinstance(node, ast.arg):
                                variable_words.update(
                                    keywords_set
                                    & ChatDialog._identifier_words(node.arg)
                                )

                score += 70 * len(class_words)
                score += 45 * len(variable_words)
            elif relative_path.lower().endswith(
                (".js", ".jsx", ".ts", ".tsx")
            ):
                for line in content.splitlines():
                    line_words = set()
                    for identifier in re.findall(r"[A-Za-z_]\w*", line):
                        line_words.update(
                            ChatDialog._identifier_words(identifier)
                        )
                    function_words.update(keywords_set & line_words)
                score += 25 * len(function_words)

            generic_matches = {
                keyword for keyword in keywords_set
                if keyword in content.casefold()
            }
            score += min(len(generic_matches), 3)

            suffix = relative_path.rsplit(".", 1)[-1].casefold()
            source_preferences = {
                "py": 8,
                "js": 7,
                "jsx": 7,
                "ts": 7,
                "tsx": 7
            }
            score += source_preferences.get(suffix, 0)
            details["score"] = score

        return sorted(
            ranked_results.items(),
            key=lambda item: (-item[1]["score"], item[0])
        )

    @staticmethod
    def _gather_code_context(reader, keywords):
        candidate_files = ChatDialog._rank_findcode_results(
            reader,
            keywords
        )[:3]
        if not candidate_files:
            return "No likely relevant files were found."

        max_context_chars = 12000
        completion_text = "Context ready."
        context_parts = ["Context gathered:"]
        used_chars = len(context_parts[0])
        function_count = 0
        for relative_path, details in candidate_files:
            try:
                content = reader.read_file(relative_path)
            except ValueError:
                continue

            if any(
                ord(character) < 32 and character not in "\t\n\f\r"
                for character in content
            ):
                continue

            if relative_path.lower().endswith(".py"):
                functions = ProjectReader.extract_python_functions(content)
                if functions is not None:
                    selected_functions = ChatDialog._select_context_functions(
                        functions,
                        details
                    )
                    if selected_functions:
                        remaining_chars = (
                            max_context_chars
                            - used_chars
                            - len(completion_text)
                            - 2
                        )
                        fitting_functions = []
                        for function in selected_functions:
                            if function_count + len(fitting_functions) >= 6:
                                break
                            candidate_functions = fitting_functions + [function]
                            file_context = (
                                ChatDialog._format_python_functions(
                                    relative_path,
                                    candidate_functions
                                )
                            )
                            if len(file_context) <= remaining_chars:
                                fitting_functions.append(function)

                        if fitting_functions:
                            file_context = ChatDialog._format_python_functions(
                                relative_path,
                                fitting_functions
                            )
                            context_parts.append(file_context)
                            used_chars += len(file_context) + 1
                            function_count += len(fitting_functions)
                            continue
                        if function_count >= 6:
                            continue

            lines = content.splitlines()
            matching_lines = [
                index for index, line in enumerate(lines)
                if any(keyword in line.casefold() for keyword in details["keywords"])
            ]
            selected_lines = set()
            for index in matching_lines:
                selected_lines.update(
                    range(max(0, index - 2), min(len(lines), index + 3))
                )

            file_lines = [relative_path]
            if selected_lines:
                ordered_lines = sorted(selected_lines)[:80]
                file_lines.extend(
                    f"{line_number + 1}: {lines[line_number]}"
                    for line_number in ordered_lines
                )
            else:
                file_lines.append("No matching content lines found.")

            file_context = "\n".join(file_lines)
            remaining_chars = (
                max_context_chars
                - used_chars
                - len(completion_text)
                - 2
            )
            if remaining_chars <= 0:
                break
            if len(file_context) > remaining_chars:
                file_context = file_context[:remaining_chars]
            context_parts.append(file_context)
            used_chars += len(file_context) + 1

        if len(context_parts) == 1:
            return "No readable matching snippets were found."
        context_parts.append(completion_text)
        return "\n".join(context_parts)

    @staticmethod
    def _gather_multi_file_context(reader, keywords):
        candidate_files = ChatDialog._rank_findcode_results(
            reader,
            keywords
        )[:3]
        if not candidate_files:
            return None, "No likely relevant files were found."

        max_context_chars = 15000
        completion_text = "Context ready."
        context_parts = ["MULTI-FILE PROJECT CONTEXT"]
        summaries = []
        used_chars = len(context_parts[0])

        for relative_path, details in candidate_files:
            try:
                content = reader.read_file(relative_path)
            except ValueError:
                continue

            if any(
                ord(character) < 32 and character not in "\t\n\f\r"
                for character in content
            ):
                continue

            selected_names = []
            functions = None
            selected_functions = []
            if relative_path.lower().endswith(".py"):
                functions = ProjectReader.extract_python_functions(content)
                if functions is not None:
                    selected_functions = (
                        ChatDialog._select_context_functions(
                            functions,
                            details,
                            relative_path,
                            query_keywords=keywords
                        )
                        [:3]
                    )
                    if selected_functions:
                        selected_names = [
                            function["qualified_name"]
                            for function in selected_functions
                        ]
                        file_context = ChatDialog._format_python_functions(
                            relative_path,
                            selected_functions
                        )
                    else:
                        file_context = ""
                else:
                    file_context = ""
            else:
                file_context = ""

            if not file_context:
                lines = content.splitlines()
                matching_lines = [
                    index for index, line in enumerate(lines)
                    if any(
                        keyword in line.casefold()
                        for keyword in details["keywords"]
                    )
                ]
                selected_lines = set()
                for index in matching_lines:
                    selected_lines.update(
                        range(max(0, index - 2), min(len(lines), index + 3))
                    )
                if not selected_lines:
                    continue
                snippets = [
                    f"{line_number + 1}: {lines[line_number]}"
                    for line_number in sorted(selected_lines)[:80]
                ]
                file_context = (
                    f"FILE: {relative_path}\n"
                    + "\n".join(snippets)
                )
                selected_names = details["matches"][:3]

            remaining_chars = (
                max_context_chars
                - used_chars
                - len(completion_text)
                - 2
            )
            if remaining_chars <= len(f"\nFILE: {relative_path}"):
                break
            if len(file_context) > remaining_chars:
                if selected_names:
                    fitting_functions = []
                    for function in (
                        selected_functions
                        if relative_path.lower().endswith(".py")
                        and functions is not None
                        else []
                    ):
                        candidate_functions = fitting_functions + [function]
                        candidate_context = (
                            ChatDialog._format_python_functions(
                                relative_path,
                                candidate_functions
                            )
                        )
                        if len(candidate_context) <= remaining_chars:
                            fitting_functions.append(function)
                    if not fitting_functions:
                        continue
                    selected_functions = fitting_functions
                    selected_names = [
                        function["qualified_name"]
                        for function in selected_functions
                    ]
                    file_context = ChatDialog._format_python_functions(
                        relative_path,
                        selected_functions
                    )
                else:
                    file_context = file_context[:remaining_chars]

            context_parts.append(file_context)
            used_chars += len(file_context) + 1
            summaries.append((relative_path, selected_names))

        if not summaries:
            return None, "No relevant readable snippets were found."

        context_parts.append(completion_text)
        summary_lines = ["Multi-file context gathered:"]
        for index, (relative_path, names) in enumerate(summaries, start=1):
            summary_lines.append(f"\n{index}. {relative_path}")
            if names:
                summary_lines.append(f"   - {'; '.join(names)}")
        summary_lines.append("\nContext ready.")
        return "\n".join(context_parts), "\n".join(summary_lines)

    @staticmethod
    def _select_context_functions(
        functions,
        details,
        relative_path=None,
        query_keywords=None
    ):
        keywords = {
            part
            for keyword in details["keywords"]
            for part in ChatDialog._identifier_words(keyword)
        }
        search_terms = set(keywords)
        query_terms = {
            part
            for keyword in query_keywords or details["keywords"]
            for part in ChatDialog._identifier_words(keyword)
        }
        project_intent = (
            "project" in query_terms
            and bool(query_terms & {
                "switch", "select", "selector", "current", "active",
                "lookup", "get"
            })
        )
        project_switching = (
            "project" in search_terms
            and bool(search_terms & {"switch", "select", "selector"})
        )
        scored_functions = []
        for function in functions:
            try:
                function_tree = ast.parse(
                    textwrap.dedent(function["source"])
                )
            except SyntaxError:
                function_tree = None

            function_name_words = ChatDialog._identifier_words(
                function["qualified_name"]
            )
            attributes = set()
            attribute_identifiers = set()
            variable_names = set()
            called_names = set()
            referenced_names = set()
            chat_dialog_constructors = []
            assigned_attributes = set()
            menu_selection_evidence = False
            chat_dialog_update = False
            chat_dialog_creation = False
            if function_tree is not None:
                for node in ast.walk(function_tree):
                    if isinstance(node, ast.Attribute):
                        attribute_identifiers.add(node.attr.casefold())
                        referenced_names.add(node.attr.casefold())
                        attributes.update(
                            ChatDialog._identifier_words(node.attr)
                        )
                    elif isinstance(node, ast.Name):
                        referenced_names.add(node.id.casefold())
                        variable_names.update(
                            ChatDialog._identifier_words(node.id)
                        )
                    elif isinstance(node, ast.arg):
                        variable_names.update(
                            ChatDialog._identifier_words(node.arg)
                        )
                        referenced_names.add(node.arg.casefold())
                    elif isinstance(node, ast.Call):
                        callable_node = node.func
                        if isinstance(
                            callable_node,
                            (ast.Name, ast.Attribute)
                        ):
                            call_name = (
                                callable_node.id
                                if isinstance(callable_node, ast.Name)
                                else callable_node.attr
                            )
                            called_names.add(call_name)

                            if (
                                isinstance(callable_node, ast.Attribute)
                                and callable_node.attr == "update_project"
                                and isinstance(callable_node.value, ast.Attribute)
                                and callable_node.value.attr == "chat_dialog"
                            ):
                                chat_dialog_update = True
                            if (
                                isinstance(callable_node, ast.Name)
                                and callable_node.id == "ChatDialog"
                            ):
                                chat_dialog_constructors.append(node)

                for node in ast.walk(function_tree):
                    if isinstance(
                        node,
                        (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)
                    ):
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        for target in targets:
                            for target_node in ast.walk(target):
                                if (
                                    isinstance(target_node, ast.Attribute)
                                    and isinstance(
                                        target_node.value,
                                        ast.Name
                                    )
                                    and target_node.value.id == "self"
                                ):
                                    if target_node.attr in {
                                        "current_project",
                                        "active_project",
                                        "selected_project"
                                    }:
                                        assigned_attributes.add(
                                            target_node.attr
                                        )

                chat_dialog_creation = any(
                    any(
                        keyword.arg == "current_project"
                        and (
                            isinstance(keyword.value, ast.Name)
                            and keyword.value.id == "current_project"
                            or isinstance(keyword.value, ast.Attribute)
                            and keyword.value.attr == "current_project"
                        )
                        for keyword in call.keywords
                    )
                    or any(
                        (
                            isinstance(argument, ast.Name)
                            and argument.id == "current_project"
                        )
                        or (
                            isinstance(argument, ast.Attribute)
                            and argument.attr == "current_project"
                        )
                        for argument in call.args
                    )
                    for call in chat_dialog_constructors
                )

                called_set = {name.casefold() for name in called_names}
                retrieval_calls = {
                    "get_projects",
                    "get_project_by_id",
                    "get_project_by_name"
                }
                selector_calls = {
                    "show_project_selector",
                    "open_project_selector",
                    "select_project",
                    "switch_project"
                }
                project_api_call = bool(called_set & (
                    retrieval_calls | selector_calls
                ))
                menu_call = bool(called_set & {"qmenu", "qaction"})
                menu_connection = bool(
                    called_set & {"connect"}
                    and referenced_names & {
                        "show_project_selector",
                        "open_project_selector",
                        "select_project",
                        "switch_project",
                        "selected_project"
                    }
                )
                menu_selection_evidence = (
                    menu_call
                    and menu_connection
                    and (
                        project_api_call
                        or bool(referenced_names & {
                            "show_project_selector",
                            "open_project_selector",
                            "select_project",
                            "switch_project"
                        })
                    )
                )

            if project_switching:
                score = 0
                switching_words = {
                    "switch", "select", "selector", "current", "active",
                    "selected"
                }
                matched_name_words = function_name_words & switching_words
                score += 25 * len(matched_name_words)

                exact_name = function["name"].casefold()
                if exact_name in {
                    "show_project_selector",
                    "open_project_selector",
                    "select_project",
                    "switch_project"
                }:
                    score += 100

                if assigned_attributes:
                    score += 160
                if "current_project" in attribute_identifiers:
                    score += 35
                if "active_project" in attribute_identifiers:
                    score += 30
                if "selected_project" in attribute_identifiers:
                    score += 30

                normalized_calls = {
                    call.casefold() for call in called_names
                }
                retrieval_evidence = normalized_calls & {
                    "get_projects",
                    "get_project_by_id",
                    "get_project_by_name"
                }
                update_evidence = normalized_calls & {"update_project"}
                selector_evidence = normalized_calls & {
                    "show_project_selector",
                    "open_project_selector",
                    "select_project",
                    "switch_project"
                }
                score += 100 * len(retrieval_evidence)
                score += 100 * len(update_evidence)
                score += 110 * len(selector_evidence)
                if chat_dialog_update:
                    score += 180
                if chat_dialog_creation:
                    score += 350
                if menu_selection_evidence:
                    score += 120

                matched_variables = variable_names & {
                    "current", "project", "active", "selected", "switch",
                    "select", "selector"
                }
                score += 12 * len(matched_variables)

                function_words = ChatDialog._identifier_words(
                    function["name"]
                )
                has_direct_flow_evidence = bool(
                    retrieval_evidence
                    or update_evidence
                    or selector_evidence
                    or chat_dialog_update
                    or chat_dialog_creation
                    or menu_selection_evidence
                )
                if function["name"].casefold() == "__init__" and not (
                    retrieval_evidence
                    or selector_evidence
                    or chat_dialog_update
                    or chat_dialog_creation
                    or menu_selection_evidence
                ):
                    score = max(score - 250, 0)
                if function_words & {
                    "task", "next", "summary", "greeting", "latest",
                    "message"
                }:
                    score -= 90
                if exact_name in {
                    "update_project_task",
                    "update_project_next_action",
                    "project_summary",
                    "show_latest_project_message"
                }:
                    score -= 140
                if (
                    exact_name == "load_settings"
                    and not has_direct_flow_evidence
                ):
                    score = max(score - 180, 0)

                has_switching_evidence = bool(
                    assigned_attributes
                    or has_direct_flow_evidence
                    or function_name_words & switching_words
                )
                if not has_switching_evidence:
                    score = 0
                if (
                    relative_path is not None
                    and relative_path.rsplit("/", 1)[-1].casefold()
                    == "database.py"
                ):
                    if exact_name in {
                        "get_projects",
                        "get_project_by_id",
                        "get_project_by_name",
                        "get_latest_project"
                    }:
                        score = max(score, 300)
                    elif exact_name in {
                        "update_project_task",
                        "update_project_next_action",
                        "update_project_field"
                    }:
                        score = min(score, 0) - 100
            else:
                score = 0
                matched_function_words = function_name_words & search_terms
                score += 30 * len(matched_function_words)
                score += 4 * len(attributes & search_terms)
                score += 6 * len(variable_names & search_terms)
                score += 10 * sum(
                    bool(ChatDialog._identifier_words(call) & search_terms)
                    for call in called_names
                )
                if project_intent:
                    score += 20 * len(
                        function_name_words & {
                            "get", "select", "selector", "current", "active",
                            "load", "open", "switch"
                        }
                    )

            if score > 0:
                scored_functions.append((function, score))

        scored_functions.sort(
            key=lambda item: (-item[1], item[0]["start_line"])
        )
        if not scored_functions:
            return []

        selected = [scored_functions[0][0]]
        selected_names = {selected[0]["name"]}
        selected_call_names = set(selected[0]["calls"])
        for function, score in scored_functions[1:]:
            if function["name"] in selected_names:
                continue
            if score >= (35 if project_switching else 30):
                selected.append(function)
                selected_names.add(function["name"])
            elif project_switching and function["name"] in selected_call_names:
                selected.append(function)
                selected_names.add(function["name"])
            if len(selected) >= 6:
                break
            selected_call_names.update(function["calls"])

        return selected

    @staticmethod
    def _format_python_functions(relative_path, functions):
        sections = [f"FILE: {relative_path}"]
        for function in functions:
            sections.append(
                f"\nFUNCTION: {function['qualified_name']}\n"
                f"{function['source']}"
            )
        return "\n".join(sections)

    @staticmethod
    def _project_identity(project):
        if project is None:
            return None
        project_id = project[0] if len(project) > 0 else None
        project_folder = project[2] if len(project) > 2 else None
        return project_id, str(project_folder).casefold() if project_folder else None

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
