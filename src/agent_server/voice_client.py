"""Simple CLI voice client for testing the /voice WebSocket endpoint.

Captures microphone audio, sends as protobuf frames, receives and plays audio responses.
"""

import asyncio
import sys
import threading
from collections.abc import Callable

import pyaudio
import websockets

# Import the generated protobuf module
# Type checker can't see into dynamically generated protobuf classes
from pipecat.frames.protobufs import frames_pb2  # type: ignore[import-untyped]

# Audio settings
# Input: 16kHz for Deepgram STT
# Output: 22050Hz for Piper TTS (ljspeech-high model)
SAMPLE_RATE_IN = 16000
SAMPLE_RATE_OUT = 22050
CHANNELS = 1
CHUNK_SIZE = 1024  # Samples per frame
FORMAT = pyaudio.paInt16  # 16-bit PCM


class AudioCapture:
    """Captures audio from microphone in a background thread."""

    def __init__(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback
        self._running = False
        self._thread: threading.Thread | None = None
        self._audio = pyaudio.PyAudio()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print("[MIC] Recording started... Speak into your microphone.")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        print("[MIC] Recording stopped.")

    def _capture_loop(self) -> None:
        stream = self._audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE_IN,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while self._running:
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                self._callback(data)
        finally:
            stream.stop_stream()
            stream.close()


class AudioPlayer:
    """Plays audio through speakers."""

    def __init__(self) -> None:
        self._audio = pyaudio.PyAudio()
        self._stream = self._audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE_OUT,
            output=True,
            frames_per_buffer=CHUNK_SIZE,
        )

    def play(self, data: bytes) -> None:
        self._stream.write(data)

    def close(self) -> None:
        self._stream.stop_stream()
        self._stream.close()


def create_audio_frame(audio_data: bytes) -> bytes:
    """Wrap raw audio bytes in a protobuf Frame message."""
    frame = frames_pb2.Frame()  # pyright: ignore[reportAttributeAccessIssue]
    frame.audio.audio = audio_data
    frame.audio.sample_rate = SAMPLE_RATE_IN
    frame.audio.num_channels = CHANNELS
    return frame.SerializeToString()


def parse_audio_frame(data: bytes) -> bytes | None:
    """Extract audio bytes from a protobuf Frame message."""
    frame = frames_pb2.Frame()  # pyright: ignore[reportAttributeAccessIssue]
    frame.ParseFromString(data)
    which = frame.WhichOneof("frame")
    if which == "audio":
        return frame.audio.audio
    if which == "text":
        print(f"[TEXT] {frame.text.text}")
        return None
    if which == "transcription":
        print(f"[STT] {frame.transcription.text}")
        return None
    return None


async def voice_client(url: str) -> None:
    """Connect to voice endpoint and stream audio bidirectionally."""
    print(f"Connecting to {url}...")

    player = AudioPlayer()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

    # Capture the event loop reference BEFORE spawning background thread
    loop = asyncio.get_running_loop()

    def on_audio_captured(data: bytes) -> None:
        # Use the captured loop reference (safe from any thread)
        loop.call_soon_threadsafe(audio_queue.put_nowait, data)

    capture = AudioCapture(callback=on_audio_captured)

    try:
        async with websockets.connect(url) as ws:
            print("Connected! Press Ctrl+C to stop.\n")
            capture.start()

            async def send_audio() -> None:
                """Send captured audio frames to server."""
                frame_count = 0
                while True:
                    audio_data = await audio_queue.get()
                    frame_bytes = create_audio_frame(audio_data)
                    await ws.send(frame_bytes)
                    frame_count += 1
                    if frame_count % 50 == 0:
                        print(f"[SEND] Sent {frame_count} audio frames")

            async def receive_audio() -> None:
                """Receive and play audio frames from server."""
                frame_count = 0
                async for message in ws:
                    if isinstance(message, bytes):
                        audio_data = parse_audio_frame(message)
                        if audio_data:
                            player.play(audio_data)
                            frame_count += 1
                            if frame_count % 10 == 0:
                                print(f"[RECV] Played {frame_count} audio frames")

            # Run send and receive concurrently
            await asyncio.gather(send_audio(), receive_audio())

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"\nConnection closed: {e}")
    finally:
        capture.stop()
        player.close()
        print("Goodbye!")


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8000/voice"
    asyncio.run(voice_client(url))


if __name__ == "__main__":
    main()
