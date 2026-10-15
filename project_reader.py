import ast
import os
import re
from pathlib import Path


class ProjectReader:
    IGNORED_FOLDERS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "coverage",
        "cache",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode"
    }
    ALLOWED_EXTENSIONS = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".html",
        ".css",
        ".json",
        ".md",
        ".txt",
        ".yml",
        ".yaml",
        ".toml"
    }
    MAX_FILE_SIZE = 200 * 1024
    SENSITIVE_FILE_NAMES = {
        "credentials",
        "credentials.json",
        "secrets",
        "secrets.json",
        "secret",
        "token",
        "tokens.json",
        "id_rsa",
        "id_ed25519",
        "private_key"
    }

    def __init__(self, project_folder):
        self.project_folder = Path(project_folder).expanduser().resolve(
            strict=True
        )
        if not self.project_folder.is_dir():
            raise NotADirectoryError("The project folder is not a directory.")

    def _is_inside_project(self, path):
        try:
            path.relative_to(self.project_folder)
            return True
        except ValueError:
            return False

    def _is_allowed_file(self, path):
        name = path.name.lower()
        if name == ".env.example":
            return True
        if name == ".env" or name.startswith(".env."):
            return False
        if name in self.SENSITIVE_FILE_NAMES:
            return False
        if path.stem.lower() in {"credentials", "secrets", "secret", "token"}:
            return False
        return path.suffix.lower() in self.ALLOWED_EXTENSIONS

    def list_files(self):
        if not self.project_folder.exists():
            raise FileNotFoundError("The project folder does not exist.")

        file_paths = []
        for current_folder, folder_names, file_names in os.walk(
            self.project_folder,
            topdown=True,
            followlinks=False
        ):
            current_path = Path(current_folder)
            folder_names[:] = [
                name for name in folder_names
                if name.lower() not in self.IGNORED_FOLDERS
                and not name.startswith(".")
                and not (current_path / name).is_symlink()
            ]

            for file_name in file_names:
                file_path = current_path / file_name
                if file_path.is_symlink() or not self._is_allowed_file(file_path):
                    continue

                try:
                    resolved_path = file_path.resolve(strict=True)
                    if not self._is_inside_project(resolved_path):
                        continue
                    if resolved_path.stat().st_size > self.MAX_FILE_SIZE:
                        continue
                except (OSError, RuntimeError):
                    continue

                file_paths.append(
                    resolved_path.relative_to(self.project_folder).as_posix()
                )

        return sorted(file_paths)

    def search_files(self, query, max_results=10):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Enter a search query.")
        if not isinstance(max_results, int) or max_results < 1:
            raise ValueError("The maximum number of results must be positive.")

        query = query.strip().casefold()
        results = []
        for relative_path in self.list_files():
            path_matches = []
            for part in Path(relative_path).parts:
                if query in part.casefold():
                    path_matches.append(part)

            content_matches = []
            file_path = self._resolve_allowed_file(relative_path)
            try:
                with file_path.open("rb") as project_file:
                    file_bytes = project_file.read(self.MAX_FILE_SIZE + 1)
                if (
                    len(file_bytes) > self.MAX_FILE_SIZE
                    or any(
                        byte < 32 and byte not in (9, 10, 12, 13)
                        for byte in file_bytes
                    )
                ):
                    continue
                content = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue

            for line in content.splitlines():
                if query not in line.casefold():
                    continue
                tokens = re.findall(r"[A-Za-z0-9_./-]+", line.strip())
                matching_tokens = [
                    token for token in tokens
                    if query in token.casefold()
                ]
                match_text = matching_tokens or [line.strip()]
                for match in match_text:
                    if match and match not in content_matches:
                        content_matches.append(match[:120])
                    if len(content_matches) >= 5:
                        break
                if len(content_matches) >= 5:
                    break

            matches = path_matches + content_matches
            if matches:
                results.append({
                    "path": relative_path,
                    "matches": matches[:5],
                    "score": len(matches)
                })

        results.sort(key=lambda result: (-result["score"], result["path"]))
        return [
            {"path": result["path"], "matches": result["matches"]}
            for result in results[:max_results]
        ]

    def read_file(self, relative_path):
        file_path = self._resolve_allowed_file(relative_path)

        try:
            with file_path.open(
                "r",
                encoding="utf-8",
                newline=""
            ) as project_file:
                return project_file.read()
        except UnicodeDecodeError as error:
            raise ValueError("That file is not readable as UTF-8 text.") from error

    @staticmethod
    def extract_python_functions(source):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        source_lines = source.splitlines()
        functions = []

        class CallCollector(ast.NodeVisitor):
            def __init__(self):
                self.names = set()

            def visit_FunctionDef(self, node):
                return

            def visit_AsyncFunctionDef(self, node):
                return

            def visit_Call(self, node):
                function = node.func
                while isinstance(function, ast.Attribute):
                    self.names.add(function.attr)
                    function = function.value
                if isinstance(function, ast.Name):
                    self.names.add(function.id)
                self.generic_visit(node)

        def visit_body(nodes, parent_names=()):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    visit_body(node.body, parent_names + (node.name,))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start_line = min(
                        [node.lineno]
                        + [decorator.lineno for decorator in node.decorator_list]
                    )
                    end_line = getattr(node, "end_lineno", node.lineno)
                    calls = CallCollector()
                    for child in node.body:
                        calls.visit(child)
                    functions.append({
                        "name": node.name,
                        "qualified_name": ".".join(parent_names + (node.name,)),
                        "start_line": start_line,
                        "end_line": end_line,
                        "source": "\n".join(
                            source_lines[start_line - 1:end_line]
                        ),
                        "calls": calls.names
                    })
                    visit_body(
                        node.body,
                        parent_names + (node.name,)
                    )

        visit_body(tree.body)
        return functions

    def _resolve_allowed_file(self, relative_path):
        requested_path = Path(relative_path)
        if requested_path.is_absolute():
            raise ValueError("Use a relative path inside the project folder.")

        file_path = (self.project_folder / requested_path).resolve(strict=True)
        if not self._is_inside_project(file_path):
            raise ValueError("That path is outside the project folder.")
        if not file_path.is_file():
            raise FileNotFoundError("That file does not exist.")
        if not self._is_allowed_file(file_path):
            raise ValueError("That file type is not allowed to be read.")
        if file_path.stat().st_size > self.MAX_FILE_SIZE:
            raise ValueError("That file is larger than the 200 KB limit.")
        return file_path

    def write_file(self, relative_path, new_content, expected_content=None):
        if not isinstance(new_content, str) or not new_content.strip():
            raise ValueError("The new file content cannot be empty.")

        file_path = self._resolve_allowed_file(relative_path)
        current_content = self.read_file(relative_path)
        if (
            expected_content is not None
            and current_content != expected_content
        ):
            raise ValueError(
                "The file changed after the proposal was generated. "
                "Generate a new proposal before approving."
            )

        new_bytes = new_content.encode("utf-8")
        if len(new_bytes) > self.MAX_FILE_SIZE:
            raise ValueError(
                "The updated file would be larger than the 200 KB limit."
            )

        file_path.write_bytes(new_bytes)

    def get_project_overview(self):
        files = self.list_files()
        return (
            f"Project folder: {self.project_folder.name}\n"
            f"Readable files: {len(files)}\n"
            "Files:\n"
            + ("\n".join(files) if files else "No readable files found.")
        )
