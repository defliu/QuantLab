import subprocess
import os

os.environ["LARKSUITE_CLI_STRICT_MODE"] = "off"

message = (
    "Project_16 Morning Review 2026-09-28: "
    "HS300 -1.24%, T=1 half-position. "
    "All 10 candidates scored <58 redline (max 55.5), NO trades today (quality first). "
    "Reason: F2 main fund (Tushare T-1 lag>2d) + F3 catalyst (no data) + F5 sector (no realtime). "
    "F2 primary = Tushare T-1 local. "
    "Capital pool: 89068.77 CNY, Buy:0/Sell:0."
)

cmd = [
    r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe",
    "im", "+messages-send",
    "--user-id", "ou_bd13444d8ea53c28249c669f43f3eeff",
    "--text", message,
]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("RC:", result.returncode)
