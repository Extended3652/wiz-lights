import asyncio
from pywizlight import wizlight

IPS = ["192.168.86.123", "192.168.86.124"]

async def main():
    bulbs = [wizlight(ip) for ip in IPS]

    try:
        for bulb in bulbs:
            await bulb.turn_on()
            print(f"ON  {bulb.ip}")
    finally:
        # Explicit cleanup prevents destructor noise
        for bulb in bulbs:
            await bulb.async_close()

if __name__ == "__main__":
    asyncio.run(main())
