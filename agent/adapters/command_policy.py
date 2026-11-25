import shlex


def safe_command_argv(command: str) -> list[str]:
    """Build an argument vector for the three supported local commands."""
    if not isinstance(command, str) or not command.strip() or len(command) > 1000:
        raise ValueError("command must contain 1 to 1000 characters")
    if any(not c.isprintable() or c in ";&|`$<>\\" for c in command):
        raise ValueError("command contains shell syntax or a control character")
    argv = shlex.split(command)
    if argv[0] == "echo":
        if any(word.startswith("-") for word in argv[1:]):
            raise ValueError("echo accepts literal text only")
    elif argv not in (["pwd"], ["ls"]):
        raise ValueError("only echo with literal text, pwd, and ls are available")
    return [f"/bin/{argv[0]}", *argv[1:]]
