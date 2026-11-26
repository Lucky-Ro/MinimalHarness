"""Textual preflight checks for Bash programs. The runner enforces OS isolation."""

import re


# These signatures reject common high-risk requests before model review. Bash
# itself handles quoting, expansion, and syntax inside the execution sandbox.
_COMMAND_START = (r"(?:^|[;&|(\n`]|\b(?:if|then|elif|else|while|until|do)\s+)"
                  r"\s*(?:(?:exec|command|env|time)\s+)*(?:/[\w./-]*/)?")
_BLOCKED = (
    (_COMMAND_START + r"(?:sudo|su|shutdown|reboot|mkfs(?:\.[\w-]+)?)(?=$|[\s;<>&|)])",
     "system administration command"),
    (_COMMAND_START + r"rm\s+(?:-[\w-]+\s+)*(?:-[a-zA-Z]*[rR]|--recursive\b)", "recursive removal"),
    (_COMMAND_START + r"dd\s+[^;&|\n]*(?:if|of)=", "raw device operation"),
    (_COMMAND_START + r"chmod\s+(?:-[a-zA-Z]+\s+)*0?777\s+/", "world-writable system path"),
    (r"(?:/etc/(?:passwd|shadow)\b|(?<![\w.-])\.(?:env|ssh)\b)", "sensitive file"),
    (r"(?<![\w.-])\.\./", "parent directory traversal"),
    (r"/dev/(?:tcp/|udp/|sd[a-z]|disk[0-9])", "network or disk device"),
    (_COMMAND_START + r"(?:nc|ncat|netcat)\s+[^;&|\n]*-[a-zA-Z]*e\b", "network command execution"),
    (_COMMAND_START + r"(?:curl|wget)\b[^;&|\n]*\|\s*(?:/[\w./-]*/)?(?:ba|da|z|k)?sh\b",
     "download piped into a shell"),
)


def validate_command(command: str) -> None:
    if not isinstance(command, str) or not command.strip() or len(command) > 1000:
        raise ValueError("command must contain 1 to 1000 characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in command):
        raise ValueError("command contains a control character")

    continued = command.replace("\\\n", "")
    for pattern, reason in _BLOCKED:
        if re.search(pattern, continued):
            raise ValueError(f"blocked by Bash policy: {reason}")
