from pathlib import Path

LIGHTS_SCRIPT = Path(__file__).resolve().with_name("lights.py")

IPS = [
    "192.168.86.123",  # kitchen 1
    "192.168.86.124",  # kitchen 2
    "192.168.86.133",  # entryway 1
    "192.168.86.134",  # entryway 2
]

ROOM_BY_IP = {
    "192.168.86.123": "KITCHEN",
    "192.168.86.124": "KITCHEN",
    "192.168.86.133": "ENTRYWAY",
    "192.168.86.134": "ENTRYWAY",
}

GROUPS = {
    "all": list(IPS),
    "kitchen": [
        "192.168.86.123",
        "192.168.86.124",
    ],
    "entryway": [
        "192.168.86.133",
        "192.168.86.134",
    ],
}

GROUP_ALIASES = {
    "kitchen": "kitchen",
    "kit": "kitchen",
    "k": "kitchen",
    "entryway": "entryway",
    "entry": "entryway",
    "e": "entryway",
    "all": "all",
    "a": "all",
}


def room_by_ip_lower() -> dict[str, str]:
    return {ip: room.lower() for ip, room in ROOM_BY_IP.items()}
