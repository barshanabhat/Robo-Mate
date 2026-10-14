import sqlite3
from datetime import datetime


class RoboMateDatabase:
    def __init__(self):
        self.database_file = "robomate.db"

    def initialize_database(self):
        connection = sqlite3.connect(self.database_file)
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    folder_path TEXT,
                    description TEXT,
                    technologies TEXT,
                    current_task TEXT,
                    next_action TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            connection.commit()
        finally:
            connection.close()

    def add_project(
        self,
        name,
        folder_path="",
        description="",
        technologies="",
        current_task="",
        next_action=""
    ):
        timestamp = datetime.now().isoformat()
        connection = sqlite3.connect(self.database_file)
        try:
            connection.execute("""
                INSERT INTO projects (
                    name, folder_path, description, technologies,
                    current_task, next_action, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name,
                folder_path,
                description,
                technologies,
                current_task,
                next_action,
                timestamp,
                timestamp
            ))
            connection.commit()
        finally:
            connection.close()

    def get_projects(self):
        connection = sqlite3.connect(self.database_file)
        try:
            cursor = connection.execute("SELECT * FROM projects")
            return cursor.fetchall()
        finally:
            connection.close()

    def get_project_by_id(self, project_id):
        connection = sqlite3.connect(self.database_file)
        try:
            cursor = connection.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,)
            )
            return cursor.fetchone()
        finally:
            connection.close()

    def get_project_by_name(self, name):
        connection = sqlite3.connect(self.database_file)
        try:
            cursor = connection.execute(
                "SELECT * FROM projects WHERE name = ? COLLATE NOCASE LIMIT 1",
                (name,)
            )
            return cursor.fetchone()
        finally:
            connection.close()

    def get_latest_project(self):
        connection = sqlite3.connect(self.database_file)
        try:
            cursor = connection.execute("""
                SELECT * FROM projects
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            return cursor.fetchone()
        finally:
            connection.close()

    def update_project_task(self, project_id, task):
        return self._update_project_field(
            project_id,
            "current_task",
            task
        )

    def update_project_next_action(self, project_id, next_action):
        return self._update_project_field(
            project_id,
            "next_action",
            next_action
        )

    def _update_project_field(self, project_id, field, value):
        if field not in {"current_task", "next_action"}:
            raise ValueError("Unsupported project field.")

        connection = sqlite3.connect(self.database_file)
        try:
            cursor = connection.execute(
                f"UPDATE projects SET {field} = ?, updated_at = ? "
                "WHERE id = ?",
                (value, datetime.now().isoformat(), project_id)
            )
            if cursor.rowcount == 0:
                raise LookupError("The selected project no longer exists.")
            connection.commit()
            return True
        finally:
            connection.close()


if __name__ == "__main__":
    database = RoboMateDatabase()
    database.initialize_database()

    connection = sqlite3.connect(database.database_file)
    try:
        existing_project = connection.execute(
            "SELECT id FROM projects WHERE name = ? LIMIT 1",
            ("HangoutHub",)
        ).fetchone()
    finally:
        connection.close()

    if existing_project is None:
        database.add_project(
            name="HangoutHub",
            folder_path="C:\\Projects\\HangoutHub",
            description="Multiplayer virtual hangout project",
            technologies="Python, React, Socket.IO",
            current_task="Testing project memory",
            next_action="Load saved project"
        )

    print(database.get_projects())
