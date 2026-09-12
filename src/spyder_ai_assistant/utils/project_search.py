"""Child-process entry point for bounded regex searches; imports no Qt."""

import json
import sys

from spyder_ai_assistant.utils.project_tools import ProjectToolsService


def main():
    request = json.load(sys.stdin)

    def emit(record):
        print(json.dumps(record), flush=True)

    try:
        service = ProjectToolsService(lambda: request["root"])
        payload, note = service._search_files(
            request["root"], request["args"], **request["limits"],
            on_match=lambda match: emit({"match": match}),
        )
    except ValueError as error:
        emit({"error": str(error)})
    else:
        emit({"payload": payload, "note": note})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
