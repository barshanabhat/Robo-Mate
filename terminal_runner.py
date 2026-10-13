import subprocess
from pathlib import Path


class CommandNotAllowedError(ValueError):
    pass


class SafeTerminalRunner:
    TIMEOUT_SECONDS = 30
    MAX_OUTPUT_CHARS = 20_000

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
