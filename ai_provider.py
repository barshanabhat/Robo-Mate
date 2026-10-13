import json
import urllib.error
import urllib.request


class OllamaProvider:
    def __init__(self):
        self.model = "qwen2.5-coder:3b"
        self.server_url = "http://localhost:11434"

    def ask(self, message, project_context=None):
        system_message = (
            "You are RoboMate, a friendly local AI coding companion.\n"
            "Help the user understand, write, and debug software.\n"
            "Explain clearly because the user is still learning.\n"
            "Do not claim you changed files unless a tool actually changed them."
        )

        if project_context is not None:
            system_message += (
                "\n\nCurrent project:"
                f"\nName: {project_context[1]}"
                f"\nDescription: {project_context[3] or ''}"
                f"\nTechnologies: {project_context[4] or ''}"
                f"\nCurrent task: {project_context[5] or ''}"
                f"\nNext action: {project_context[6] or ''}"
            )

        request_data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": message}
            ],
            "stream": False
        }
        request = urllib.request.Request(
            f"{self.server_url}/api/chat",
            data=json.dumps(request_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["message"]["content"]
        except urllib.error.HTTPError as error:
            error_message = error.read().decode("utf-8", errors="replace")
            if error.code == 404 and "not found" in error_message.lower():
                return (
                    f"The Ollama model '{self.model}' is not installed. "
                    f"Run 'ollama pull {self.model}' to install it."
                )
            return "I couldn't get a response from Ollama. Please try again."
        except (urllib.error.URLError, TimeoutError, OSError):
            return (
                "I can't reach the local Ollama service. "
                "Make sure Ollama is running."
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return "Ollama returned an invalid response. Please try again."
