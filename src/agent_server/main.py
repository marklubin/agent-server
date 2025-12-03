"""Main entry point for the agent server."""

import logging
import os
import uuid

import aiohttp
import dotenv
import uvicorn
from deepgram import LiveOptions
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat_whisker import WhiskerObserver

from agent_server.model import InputChunk, ResponseChunk, ResponseDone, ResponseStart
from agent_server.pipecat import LettaLLMService, UserTurnAggregator
from agent_server.provider import AnthropicProvider, LettaProvider

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI()


def get_or_die(env_var: str) -> str:
    maybe_env_var = os.environ.get(env_var)
    if maybe_env_var is None:
        raise RuntimeError(f"Missing environment variable {env_var}")
    return maybe_env_var


# Config from environment
agent_id = get_or_die("LETTA_AGENT_ID")
deepgram_api_key = get_or_die("DEEPGRAM_API_KEY")


anthropic_provider = AnthropicProvider()
letta_provider = LettaProvider(agent_id=agent_id)


@app.get("/hello")
async def hello() -> str:
    return "hello world"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            text = await websocket.receive_text()
            input_chunk = InputChunk.model_validate_json(text)
            logger.info(f"Received input chunk: {input_chunk.text}")

            response_id = f"response-{uuid.uuid4()}"
            logger.info("Sending response start")
            response_start = ResponseStart(id=response_id, timestamp=1)
            await websocket.send_text(response_start.model_dump_json())

            chunk_cnt = 0
            async for chunk in letta_provider.stream_response(user_message=input_chunk.text):
                logger.info(f"Received chunk {chunk_cnt}. Content: {chunk}")
                response_chunk = ResponseChunk(
                    chunk_id=f"chunk-{chunk_cnt}",
                    response_id=response_id,
                    timestamp=2,
                    text=chunk,
                )
                await websocket.send_text(response_chunk.model_dump_json())
                chunk_cnt += 1

            logger.info("Sending response end")
            response_done = ResponseDone(id=response_id, timestamp=3)
            await websocket.send_text(response_done.model_dump_json())
    except WebSocketDisconnect:
        logger.info("Disconnected from websocket")


@app.websocket("/voice")
async def voice_endpoint(websocket: WebSocket) -> None:
    """Voice pipeline endpoint using Pipecat."""
    await websocket.accept()

    # Create transport for this WebSocket connection
    # ProtobufFrameSerializer defines the wire format for audio/text frames
    # VAD config: be patient with pauses, don't cut off mid-sentence
    vad = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=1.5,  # Wait 1.5s of silence before "done speaking" (default 0.8)
        )
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=vad,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    # Create services
    # Deepgram STT with generous utterance detection
    stt = DeepgramSTTService(
        api_key=deepgram_api_key,
        live_options=LiveOptions(
            model="nova-2",
            language="en-US",
            punctuate=True,
            interim_results=True,
            utterance_end_ms="2000",  # Wait 2s of silence before finalizing (default ~1s)
            vad_events=True,
        ),
    )

    user_turn_aggregator = UserTurnAggregator()

    llm = LettaLLMService(agent_id=agent_id, name="letta")

    async with aiohttp.ClientSession() as session:
        tts = PiperTTSService(
            base_url="http://localhost:5001",
            aiohttp_session=session,
        )

        # Build the pipeline
        pipeline = Pipeline(
            [
                transport.input(),  # Audio from client
                stt,  # Speech-to-text
                user_turn_aggregator,
                llm,  # Letta LLM
                tts,  # Text-to-speech
                transport.output(),  # Audio back to client
            ]
        )

        # WhiskerObserver starts a WebSocket server on port 9090 for the Whisker debugger
        whisker = WhiskerObserver(pipeline)

        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=True),
            observers=[whisker],
        )

        runner = PipelineRunner()
        await runner.run(task)


def main() -> None:
    """Run the agent server."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting agent server...")
    uvicorn.run("agent_server.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
