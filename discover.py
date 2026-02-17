import asyncio
from pywizlight.discovery import discover_lights

async def main():
    print("Discovering WiZ bulbs...")
    bulbs = await discover_lights(broadcast_space="255.255.255.255")

    if not bulbs:
        print("No bulbs found.")
        return

    for b in bulbs:
        print(f"IP: {b.ip}  MAC: {b.mac}  MODEL: {b.model}")

asyncio.run(main())
