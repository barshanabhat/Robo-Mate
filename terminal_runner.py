import subprocess
from pathlib import Path


class CommandNotAllowedError(ValueError):
    pass


class SafeTerminalRunner:
    TIMEOUT_SECONDS = 30
    MAX_OUTPUT_CHARS = 20_000
    BLOCKED_MESSAGE_OPERATORS = ("&", "|", ";", ">", "<", "`", "$")

    ALLOWED_COMMANDS = {
        "python --version": ("python", "--version"),
        "python -m pytest": ("python", "-m", "pytest"),
        "pytest": ("pytest",),
        "npm test": ("npm", "test"),
        "npm run test": ("npm", "run", "test"),
        "npm run build": ("npm", "run", "build"),
        "npm run lint": ("npm", "run", "lint"),
        "git status": ("git", "status"),
        "git diff": ("git", "diff"),
        "git log --oneline -5": ("git", "log", "--oneline", "-5")
    }

    BLOCKED_OPERATORS = ("&", "|", ";", ">", "<", "`", "$")

    def __init__(self, project_folder):
        self.project_folder = Path(project_folder).expanduser().resolve(
            strict=True
        )
        if not self.project_folder.is_dir():
            raise NotADirectoryError(
                "The selected project folder is not a directory."
            )

    @classmethod
    def validate_command(cls, command):
        if not isinstance(command, str):
            raise CommandNotAllowedError("Commands must be plain text.")

        normalized_command = " ".join(command.strip().lower().split())
        if not normalized_command:
            raise CommandNotAllowedError("Enter a command after /run.")

        if any(operator in command for operator in cls.BLOCKED_OPERATORS):
            raise CommandNotAllowedError(
                "Shell operators and command chaining are not allowed."
            )

        if normalized_command not in cls.ALLOWED_COMMANDS:
            raise CommandNotAllowedError(
                "That command is not on RoboMate's safe command allowlist."
            )

        return cls.ALLOWED_COMMANDS[normalized_command]

    @classmethod
    def _limit_output(cls, output):
        if len(output) <= cls.MAX_OUTPUT_CHARS:
            return output
        return output[:cls.MAX_OUTPUT_CHARS] + "\n[output truncated]"

    @staticmethod
    def _decode_output(output):
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output or ""

    def run(self, command):
        arguments = self.validate_command(command)
        try:
            result = subprocess.run(
                arguments,
                cwd=self.project_folder,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.TIMEOUT_SECONDS,
                check=False
            )
        except subprocess.TimeoutExpired as error:
            output = (
                self._decode_output(error.stdout)
                + self._decode_output(error.stderr)
            )
            if output:
                return self._limit_output(
                    f"{output.rstrip()}\n\nCommand timed out."
                )
            return "Command timed out."
        except FileNotFoundError as error:
            raise FileNotFoundError(
                "The command executable was not found."
            ) from error
        except PermissionError as error:
            raise PermissionError(
                "RoboMate does not have permission to run that command."
            ) from error
        except OSError:
            raise

        output = result.stdout + result.stderr
        if not output.strip():
            output = "Command completed without output."
        if result.returncode:
            output = (
                f"Command failed with exit code {result.returncode}.\n"
                f"{output}"
            )
        return self._limit_output(output)

    @classmethod
    def validate_commit_message(cls, message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Enter a non-empty commit message.")

        message = message.strip()
        if len(message) > 120:
            raise ValueError("Commit messages must be 120 characters or fewer.")
        if any(operator in message for operator in cls.BLOCKED_MESSAGE_OPERATORS):
            raise ValueError("Commit messages cannot contain shell operators.")
        if any(ord(character) < 32 or ord(character) == 127 for character in message):
            raise ValueError("Commit messages cannot contain control characters.")
        return message

    def _run_git(self, *arguments):
        try:
            return subprocess.run(
                ("git", *arguments),
                cwd=self.project_folder,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.TIMEOUT_SECONDS,
                check=False
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("Command timed out.") from error
        except FileNotFoundError as error:
            raise FileNotFoundError(
                "The Git executable was not found."
            ) from error
        except PermissionError as error:
            raise PermissionError(
                "RoboMate does not have permission to run Git."
            ) from error

    def _ensure_git_repository(self):
        result = self._run_git("rev-parse", "--is-inside-work-tree")
        if result.returncode or result.stdout.strip().lower() != "true":
            raise ValueError("The selected project folder is not a Git repository.")

    @staticmethod
    def _git_error(result):
        output = (result.stderr or result.stdout).strip()
        if output:
            return f"Git command failed:\n{output}"
        return f"Git command failed with exit code {result.returncode}."

    def git_status(self):
        self._ensure_git_repository()
        result = self._run_git("status", "--short")
        if result.returncode:
            raise RuntimeError(self._git_error(result))

        status = result.stdout.strip()
        if not status:
            return "Working tree is clean."
        return self._limit_output(status)

    def checkpoint(self, message):
        message = self.validate_commit_message(message)
        self._ensure_git_repository()

        status = self._run_git("status", "--short")
        if status.returncode:
            raise RuntimeError(self._git_error(status))
        if not status.stdout.strip():
            return "No changes to checkpoint."

        staged = self._run_git("add", "-A")
        if staged.returncode:
            raise RuntimeError(self._git_error(staged))

        commit = self._run_git("commit", "-m", message)
        if commit.returncode:
            raise RuntimeError(self._git_error(commit))

        output = (commit.stdout + commit.stderr).strip()
        if not output:
            output = "Commit completed successfully."
        return self._limit_output(f"Checkpoint created:\n{output}")
