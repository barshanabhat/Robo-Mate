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
        r".venv\scripts\python.exe -m pytest": (
            r".venv\Scripts\python.exe",
            "-m",
            "pytest"
        ),
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
    PYTHON_VENV_TEST_COMMAND = r".venv\Scripts\python.exe -m pytest"

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
            result = self._run_arguments(arguments, cwd=self.project_folder)
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
    def _run_arguments(cls, arguments, cwd=None):
        return subprocess.run(
            arguments,
            cwd=cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cls.TIMEOUT_SECONDS,
            check=False
        )

    def detect_test_command(self):
        root_files = {
            path.name.lower()
            for path in self.project_folder.iterdir()
            if path.is_file()
        }
        has_python = bool(
            root_files.intersection({
                "pyproject.toml",
                "pytest.ini",
                "setup.cfg",
                "requirements.txt"
            })
            or any(name.endswith(".py") for name in root_files)
        )
        if has_python:
            venv_python = (
                self.project_folder
                / ".venv"
                / "Scripts"
                / "python.exe"
            )
            command = (
                self.PYTHON_VENV_TEST_COMMAND
                if venv_python.is_file()
                else "python -m pytest"
            )
        elif "package.json" in root_files:
            command = "npm test"
        else:
            return None

        self.validate_command(command)
        return command

    def run_tests(self):
        command, result = self._execute_tests()
        if command is None:
            return "I couldn't find a supported test command for this project."

        if isinstance(result, subprocess.TimeoutExpired):
            stdout = self._decode_output(result.stdout).strip() or "(no output)"
            stderr = self._decode_output(result.stderr).strip() or "(no output)"
            output = (
                f"Test command:\n{command}\n\n"
                "Exit code: unavailable (timed out)\n\n"
                "Tests timed out.\n\n"
                f"stdout:\n{stdout}\n\n"
                f"stderr:\n{stderr}"
            )
            return self._limit_output(output)

        stdout = result.stdout.strip() or "(no output)"
        stderr = result.stderr.strip() or "(no output)"
        missing_venv_pytest = (
            command == self.PYTHON_VENV_TEST_COMMAND
            and result.returncode != 0
            and "no module named" in stderr.lower()
            and "pytest" in stderr.lower()
        )
        if result.returncode == 0:
            status = "Tests passed."
        elif missing_venv_pytest:
            status = (
                "Pytest is not installed in this project's Python environment."
            )
        else:
            status = "Tests failed."
        output = (
            f"Test command:\n{command}\n\n"
            f"Exit code: {result.returncode}\n\n"
            f"{status}\n\n"
            f"stdout:\n{stdout}\n\n"
            f"stderr:\n{stderr}"
        )
        return self._limit_output(output)

    def _execute_tests(self):
        command = self.detect_test_command()
        if command is None:
            return None, None

        arguments = self.validate_command(command)
        try:
            result = self._run_arguments(arguments, cwd=self.project_folder)
        except subprocess.TimeoutExpired as error:
            return command, error
        return command, result

    def verify(self):
        diff_stat = self._run_git("diff", "--stat")
        if diff_stat.returncode:
            raise RuntimeError(self._git_error(diff_stat))

        diff = self._run_git("diff")
        if diff.returncode:
            raise RuntimeError(self._git_error(diff))
        if not diff.stdout.strip():
            return "No uncommitted code changes to verify."

        command, test_result = self._execute_tests()
        if command is None:
            test_report = (
                "I couldn't find a supported test command for this project."
            )
            passed = False
        elif isinstance(test_result, subprocess.TimeoutExpired):
            stdout = self._decode_output(test_result.stdout).strip() or "(no output)"
            stderr = self._decode_output(test_result.stderr).strip() or "(no output)"
            test_report = (
                f"Test command: {command}\n"
                "Exit code: unavailable (timed out)\n"
                "Tests timed out.\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )
            passed = False
        else:
            stdout = test_result.stdout.strip() or "(no output)"
            stderr = test_result.stderr.strip() or "(no output)"
            test_report = (
                f"Test command: {command}\n"
                f"Exit code: {test_result.returncode}\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )
            passed = test_result.returncode == 0

        result_message = (
            "✅ Verification passed."
            if passed
            else (
                "❌ Verification failed.\n"
                "Review the test output before creating a checkpoint."
            )
        )
        report = (
            "Verification\n\n"
            "Changed files:\n"
            f"{diff_stat.stdout.strip() or '(no diff stat available)'}\n\n"
            f"Tests:\n{test_report}\n\n"
            f"Result:\n{result_message}"
        )
        return self._limit_output(report)

    def workflow_status(self, verification_passed=False):
        repository = self._run_git("rev-parse", "--is-inside-work-tree")
        if (
            repository.returncode
            or repository.stdout.strip().lower() != "true"
        ):
            return (
                "Workflow status:\n\n"
                "Git repository: No\n"
                "Changes: Unknown\n"
                "Verified: No\n"
                "Checkpoint: Unknown\n"
                "Push: Unknown\n\n"
                "Recommended next step:\n"
                "Select a Git repository to use the safe Git workflow."
            )

        status = self._run_git("status", "--short")
        if status.returncode:
            raise RuntimeError(self._git_error(status))
        has_changes = bool(status.stdout.strip())

        upstream = self._run_git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}"
        )
        has_upstream = upstream.returncode == 0 and bool(
            upstream.stdout.strip()
        )
        upstream_name = upstream.stdout.strip() if has_upstream else None
        ahead_count = None
        behind_count = None
        if has_upstream:
            divergence = self._run_git(
                "rev-list",
                "--left-right",
                "--count",
                "HEAD...@{u}"
            )
            if divergence.returncode:
                raise RuntimeError(self._git_error(divergence))
            try:
                counts = divergence.stdout.split()
                if len(counts) != 2:
                    raise ValueError
                ahead_count, behind_count = map(int, counts)
                if ahead_count < 0 or behind_count < 0:
                    raise ValueError
            except ValueError as error:
                raise RuntimeError(
                    "Git returned invalid ahead/behind counts."
                ) from error

        verified = has_changes and verification_passed
        if has_changes:
            recommendation = (
                "/checkpoint <commit message>"
                if verified
                else "/verify"
            )
            push_state = "Not ready"
            checkpoint_state = "Not created"
        elif behind_count is not None and behind_count > 0:
            recommendation = (
                "Remote has newer commits. Manual review is recommended "
                "before pulling."
            )
            push_state = (
                f"{ahead_count} local commit(s) pending"
                if ahead_count
                else "No local commits pending"
            )
            checkpoint_state = "No pending changes"
        elif ahead_count is not None and ahead_count > 0:
            recommendation = "/push"
            push_state = f"{ahead_count} local commit(s) pending"
            checkpoint_state = "Committed"
        elif not has_upstream:
            recommendation = (
                "Configure an upstream remote before using /push."
            )
            push_state = "No upstream configured"
            checkpoint_state = "No pending changes"
        else:
            recommendation = "Project is clean and up to date."
            push_state = "Up to date"
            checkpoint_state = "No pending changes"

        report = (
            "Workflow status:\n\n"
            "Git repository: Yes\n"
            f"Changes: {'Yes' if has_changes else 'No'}\n"
            f"Verified: {'Yes' if verified else 'No'}\n"
            f"Upstream: {upstream_name or 'Not configured'}\n"
            f"Ahead: {ahead_count if ahead_count is not None else 'Unknown'}\n"
            f"Behind: {behind_count if behind_count is not None else 'Unknown'}\n"
            f"Checkpoint: {checkpoint_state}\n"
            f"Push: {push_state}\n\n"
            f"Recommended next step:\n{recommendation}"
        )
        return self._limit_output(report)

    def git_summary(self):
        def run_read_only_git(*arguments):
            try:
                return self._run_git(*arguments)
            except (OSError, TimeoutError):
                return None

        repository = run_read_only_git(
            "rev-parse",
            "--is-inside-work-tree"
        )
        if (
            repository is None
            or repository.returncode
            or repository.stdout.strip().lower() != "true"
        ):
            repo_message = (
                ""
                if repository is None
                else (repository.stderr or repository.stdout).lower()
            )
            is_not_repository = "not a git repository" in repo_message
            return {
                "repository": (
                    "No" if is_not_repository else "Unknown"
                ),
                "branch": "Not available",
                "upstream": "Not configured",
                "ahead": "Unknown",
                "behind": "Unknown",
                "changes": "Unknown",
                "latest_commit": "Not available"
            }

        summary = {
            "repository": "Yes",
            "branch": "Unknown",
            "upstream": "Not configured",
            "ahead": "Unknown",
            "behind": "Unknown",
            "changes": "Unknown",
            "latest_commit": "Not available"
        }

        branch = run_read_only_git("branch", "--show-current")
        if branch is not None and branch.returncode == 0 and branch.stdout.strip():
            summary["branch"] = branch.stdout.strip()

        status = run_read_only_git("status", "--short")
        if status is not None and status.returncode == 0:
            summary["changes"] = (
                "Yes" if status.stdout.strip() else "No"
            )

        upstream = run_read_only_git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}"
        )
        if (
            upstream is not None
            and upstream.returncode == 0
            and upstream.stdout.strip()
        ):
            summary["upstream"] = upstream.stdout.strip()
            divergence = run_read_only_git(
                "rev-list",
                "--left-right",
                "--count",
                "HEAD...@{u}"
            )
            if divergence is not None and divergence.returncode == 0:
                counts = divergence.stdout.split()
                if len(counts) == 2:
                    try:
                        ahead, behind = map(int, counts)
                        if ahead >= 0 and behind >= 0:
                            summary["ahead"] = str(ahead)
                            summary["behind"] = str(behind)
                    except ValueError:
                        pass

        latest_commit = run_read_only_git("log", "-1", "--oneline")
        if (
            latest_commit is not None
            and latest_commit.returncode == 0
            and latest_commit.stdout.strip()
        ):
            summary["latest_commit"] = latest_commit.stdout.strip()

        return summary

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

    def push(self):
        self._ensure_git_repository()

        branch = self._run_git("branch", "--show-current")
        if branch.returncode:
            raise RuntimeError(self._git_error(branch))
        branch_name = branch.stdout.strip()
        if not branch_name:
            return "Cannot push while HEAD is detached."

        upstream = self._run_git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}"
        )
        if upstream.returncode or not upstream.stdout.strip():
            return "This branch has no upstream remote yet."

        upstream_details = self._run_git(
            "for-each-ref",
            "--format=%(upstream:remotename)%00%(upstream:remoteref)",
            f"refs/heads/{branch_name}"
        )
        if upstream_details.returncode:
            raise RuntimeError(self._git_error(upstream_details))

        details = upstream_details.stdout.strip().split("\0", 1)
        if (
            len(details) != 2
            or not details[0]
            or details[0] == "."
            or not details[1].startswith("refs/heads/")
        ):
            return "This branch has no upstream remote yet."
        remote_name, remote_branch = details

        status = self._run_git("status", "--short")
        if status.returncode:
            raise RuntimeError(self._git_error(status))
        if status.stdout.strip():
            return "You have uncommitted changes. Create a checkpoint first."

        result = self._run_git(
            "push",
            "--",
            remote_name,
            f"HEAD:{remote_branch}"
        )
        if result.returncode:
            raise RuntimeError(self._git_error(result))

        output = (result.stdout + result.stderr).strip()
        if not output:
            output = "Push completed successfully."
        return self._limit_output(output)
