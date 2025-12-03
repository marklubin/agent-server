"""Simple WebSocket test client for the agent server."""

import asyncio
import sys
import time

import websockets


async def main() -> None:
    """Connect to server and send input chunks on each line."""
    uri = "ws://localhost:8000/ws"

    async with websockets.connect(uri) as ws:
        print(f"Connected to {uri}")
        print("Type messages and press Enter to send as InputChunk")
        print("Ctrl+C to exit")
        print("-" * 40)

        async def receive_messages() -> None:
            """Print all messages from server."""
            try:
                async for message in ws:
                    print(f"← {message}")
            except websockets.ConnectionClosed:
                print("Connection closed")

        async def send_messages() -> None:
            """Read stdin and send as InputChunk."""
            loop = asyncio.get_event_loop()
            while True:
                # Read line from stdin without blocking event loop
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                text = line.rstrip("\n")
                if text:
                    chunk = f'{{"type": "input_chunk", "text": "{text}", "timestamp": {time.time()}}}'
                    await ws.send(chunk)
                    print(f"→ {chunk}")

        # Run send and receive concurrently
        await asyncio.gather(
            receive_messages(),
            send_messages(),
        )


def run() -> None:
    """Entry point for uv script."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    run()
