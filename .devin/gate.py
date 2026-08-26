"""Devin Stop hook gate: run pytest and block until it passes or the turn ceiling is hit."""
import json
import os
import subprocess
import sys

MAX_TURNS = 10
TURN_FILE = os.path.join(os.path.dirname(__file__), ".loop_turns")


def get_turn() -> int:
    try:
        with open(TURN_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except (FileNotFoundError, ValueError):
        return 0


def set_turn(n: int) -> None:
    with open(TURN_FILE, "w", encoding="utf-8") as f:
        f.write(str(n))


def reset() -> None:
    try:
        os.remove(TURN_FILE)
    except FileNotFoundError:
        pass


def main() -> None:
    # Read stdin but we do not strictly need it for the gate.
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    turn = get_turn() + 1
    set_turn(turn)

    if turn > MAX_TURNS:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Hit the turn ceiling ({MAX_TURNS}). Stopping to avoid an open loop.",
                }
            )
        )
        reset()
        return

    result = subprocess.run(
        [sys.executable, "-m", "pytest"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(
            json.dumps(
                {"decision": "approve", "reason": f"All tests passed on turn {turn}."}
            )
        )
        reset()
        return

    # Tests failed — block and show the agent why.
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": f"Tests failed on turn {turn}/{MAX_TURNS}. Read the pytest output, fix the issue, and continue.",
            }
        )
    )
    if result.stdout:
        sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


if __name__ == "__main__":
    main()
