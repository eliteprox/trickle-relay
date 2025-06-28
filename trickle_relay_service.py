#!/usr/bin/env python3
"""
Trickle Relay Service

A standalone microservice that acts as a bridge between trickle HTTP segments
and WebRTC connections to ComfyStream.

Flow:
1. Subscribe to input trickle stream
2. Decode trickle segments to video frames
3. Send frames to ComfyStream via WebRTC
4. Receive processed frames from ComfyStream
5. Encode processed frames to trickle segments
6. Publish output trickle stream

This service is completely separate from ComfyStream and uses its own codebase.
"""

import asyncio
import aiohttp
import json
import logging
import uuid
import time
import av
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

# WebRTC imports
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
    MediaStreamTrack
)

logger = logging.getLogger(__name__)

# Remove timestamp modification completely - true passthrough

@dataclass
class RelaySession:
    """Represents a trickle-to-WebRTC relay session"""
    session_id: str
    input_stream_url: str
    output_stream_url: str
    comfystream_url: str
    prompts: List[Dict[str, Any]]
    width: int = 512
    height: int = 512
    status: str = 'starting'
    created_at: datetime = None

    # Processing tasks
    subscriber_task: Optional[asyncio.Task] = None
    webrtc_task: Optional[asyncio.Task] = None
    publisher_task: Optional[asyncio.Task] = None

        # Data queues
    decoded_frame_queue: Optional[asyncio.Queue] = None
    processed_frame_queue: Optional[asyncio.Queue] = None
    output_segment_queue: Optional[asyncio.Queue] = None

    # WebRTC components
    peer_connection: Optional[RTCPeerConnection] = None
    data_channel: Optional[Any] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

class VideoStreamTrack(MediaStreamTrack):
    """Video track that sends frames to ComfyStream via WebRTC"""

    kind = "video"

    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True

    async def recv(self):
        """Get next frame for H.264 passthrough - ABSOLUTE FRAME INTEGRITY PRESERVATION"""
        try:
            if not self._running:
                raise Exception("Track stopped")

            # CRITICAL: Get frame without ANY modifications to preserve H.264 NAL units
            frame = await self.frame_queue.get()

            if frame is None:
                self._running = False
                raise Exception("End of stream")

            # ABSOLUTE PASSTHROUGH: Do NOT modify ANYTHING about the frame
            # No format changes, no timing changes, no property modifications
            # Any changes can corrupt H.264 NAL unit structure and cause:
            # - "No start code is found" errors
            # - "Error splitting the input into NAL units" errors
            # - "Missing reference picture" errors
            # - "illegal short term buffer state" errors

            logger.debug(f"H.264 passthrough frame: {frame.width}x{frame.height}, format={frame.format.name}, pts={frame.pts}, time_base={frame.time_base}")

            # Return frame exactly as received from decoder
            return frame

        except Exception as e:
            logger.error(f"VideoTrack recv error: {e}")
            raise

    def stop(self):
        self._running = False

class WebRTCClient:
    """WebRTC client for connecting to ComfyStream"""

    def __init__(self, comfystream_url: str):
        self.comfystream_url = comfystream_url
        self.session = aiohttp.ClientSession()

    async def create_connection(self, prompts: List[Dict], width: int, height: int,
                              input_track: VideoStreamTrack,
                              output_queue: asyncio.Queue) -> tuple[RTCPeerConnection, Any]:
        """Create WebRTC connection to ComfyStream"""
        try:
            # Create peer connection with configurable ICE servers
            configuration = RTCConfiguration(
                iceServers=[
                    # Configure STUN server with specific port (default uses random ports)
                    RTCIceServer(urls="stun:stun.l.google.com:19302"),
                    # You can add TURN servers here with specific ports
                    # RTCIceServer(
                    #     urls="turn:your-turn-server.com:5678",
                    #     username="your-username",
                    #     credential="your-password"
                    # )
                ]
            )
            pc = RTCPeerConnection(configuration)

            # Add input track (our decoded frames)
            pc.addTrack(input_track)

            # CRITICAL: Ensure H.264 codec is preferred for ComfyStream compatibility
            from aiortc.rtcrtpsender import RTCRtpSender
            try:
                caps = RTCRtpSender.getCapabilities("video")
                h264_codecs = [codec for codec in caps.codecs if codec.name == "H264"]
                if h264_codecs:
                    logger.info(f"H.264 codec available: {h264_codecs[0].name} - this should prevent NAL unit errors")
                else:
                    logger.warning("No H.264 codecs available - this may cause NAL unit errors in ComfyStream")
            except Exception as e:
                logger.warning(f"Could not check H.264 codec availability: {e}")

            # Create data channel for control messages
            data_channel = pc.createDataChannel("control")

            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track from ComfyStream: {track.kind}")
                if track.kind == "video":
                    # Process incoming frames in background task
                    async def process_incoming_frames():
                        processed_count = 0
                        try:
                            while True:
                                frame = await track.recv()
                                # PRESERVE ALL PROCESSED FRAMES - no dropping
                                await output_queue.put(frame)
                                processed_count += 1
                                if processed_count % 100 == 0:
                                    logger.debug(f"Processed {processed_count} frames from ComfyStream")
                        except Exception as e:
                            logger.error(f"Error processing incoming frames: {e}")

                    asyncio.create_task(process_incoming_frames())

            @data_channel.on("open")
            def on_data_channel_open():
                logger.info("Data channel opened")

                # Send resolution update
                resolution_msg = {
                    "type": "update_resolution",
                    "width": width,
                    "height": height
                }
                data_channel.send(json.dumps(resolution_msg))
                logger.info(f"Sent resolution update: {width}x{height}")

            @data_channel.on("message")
            def on_data_channel_message(message):
                logger.info(f"Received control message: {message}")

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Send offer to ComfyStream
            offer_data = {
                "prompts": prompts,
                "offer": {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                }
            }

            logger.info(f"Sending WebRTC offer to {self.comfystream_url}/offer")

            async with self.session.post(
                f"{self.comfystream_url}/offer",
                json=offer_data,
                headers={"Content-Type": "application/json"}
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"ComfyStream offer failed: {response.status} - {error_text}")

                answer_data = await response.json()

            # Set remote description
            answer = RTCSessionDescription(
                sdp=answer_data["sdp"],
                type=answer_data["type"]
            )
            await pc.setRemoteDescription(answer)

            logger.info("WebRTC connection established successfully")
            return pc, data_channel

        except Exception as e:
            logger.error(f"Error creating WebRTC connection: {e}")
            raise

    async def close(self):
        """Close the WebRTC client"""
        if self.session:
            await self.session.close()

class TrickleRelayService:
    """Main trickle relay service"""

    def __init__(self, bind_host: str = "0.0.0.0", bind_port: int = 8890):
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.active_sessions: Dict[str, RelaySession] = {}

    async def start_session(self, session_config: Dict[str, Any]) -> str:
        """Start a new relay session"""
        session_id = session_config.get('session_id', str(uuid.uuid4()))

        # Validate required fields
        required_fields = ['input_stream_url', 'comfystream_url', 'prompts']
        for field in required_fields:
            if field not in session_config:
                raise ValueError(f"Missing required field: {field}")

        # Create session with processed output stream URL
        input_url = session_config['input_stream_url']
        default_output_url = f"{input_url}-processed" if not input_url.endswith('/') else f"{input_url}processed"

        session = RelaySession(
            session_id=session_id,
            input_stream_url=input_url,
            output_stream_url=session_config.get('output_stream_url', default_output_url),
            comfystream_url=session_config['comfystream_url'],
            prompts=session_config['prompts'],
            width=session_config.get('width', 512),  # For data channel only
            height=session_config.get('height', 512)  # For data channel only
        )

        # Initialize ADAPTIVE queues - NO HARD LIMITS to prevent frame dropping
        # Queues will grow as needed based on processing speed differences
        session.decoded_frame_queue = asyncio.Queue()      # UNLIMITED - no maxsize
        session.processed_frame_queue = asyncio.Queue()    # UNLIMITED - no maxsize
        session.output_segment_queue = asyncio.Queue()     # UNLIMITED - no maxsize

        # Store session
        self.active_sessions[session_id] = session

        # Start processing tasks
        session.subscriber_task = asyncio.create_task(
            self._run_trickle_subscriber(session)
        )

        session.webrtc_task = asyncio.create_task(
            self._run_webrtc_processing(session)
        )

        session.publisher_task = asyncio.create_task(
            self._run_trickle_publisher(session)
        )

        session.status = 'active'
        logger.info(f"Started relay session {session_id}")

        return session_id

    async def _run_trickle_subscriber(self, session: RelaySession):
        """Subscribe to input trickle stream with PARALLEL segment fetching and decoding pipeline"""
        from trickle_components import TrickleSubscriber, TrickleStreamDecoder

        try:
            logger.info(f"Starting PIPELINED trickle subscriber for {session.input_stream_url}")

            # PIPELINE: Separate queues for different stages
            raw_segment_queue = asyncio.Queue()        # UNLIMITED - no segment dropping
            decoded_frames_buffer = asyncio.Queue()    # UNLIMITED - no frame dropping

            # Passthrough decoder - PRESERVE ORIGINAL H.264 FORMAT AND DIMENSIONS
            # Do not specify target dimensions to avoid any format conversions
            decoder = TrickleStreamDecoder(target_width=None, target_height=None)

            # TASK 1: SEGMENT FETCHER - Continuously fetch segments
            async def segment_fetcher():
                """Continuously fetch segments without blocking on decoding"""
                try:
                    async with TrickleSubscriber(session.input_stream_url) as subscriber:
                        consecutive_empty = 0
                        max_consecutive_empty = 50

                        while session.status == 'active':
                            try:
                                # Get next segment (non-blocking w.r.t. decoding)
                                segment_reader = await subscriber.next()
                                if segment_reader is None:
                                    consecutive_empty += 1
                                    if consecutive_empty >= max_consecutive_empty:
                                        logger.info("No more segments available")
                                        break
                                    await asyncio.sleep(0.001)  # 1ms
                                    continue

                                consecutive_empty = 0

                                # Read complete segment data
                                segment_data = b""
                                while True:
                                    chunk = await segment_reader.read(65536)  # 64KB chunks
                                    if not chunk:
                                        break
                                    segment_data += chunk

                                await segment_reader.close()

                                if segment_data:
                                    # Queue raw segment for processing - NO DROPPING
                                    await raw_segment_queue.put(segment_data)
                                    logger.debug(f"Fetched segment ({len(segment_data)} bytes)")

                            except Exception as e:
                                logger.error(f"Error in segment fetcher: {e}")
                                await asyncio.sleep(0.005)

                    # Signal end of segments
                    await raw_segment_queue.put(None)
                    logger.info("Segment fetcher finished")

                except Exception as e:
                    logger.error(f"Segment fetcher error: {e}")

            # TASK 2: SEGMENT DECODER - Continuously decode segments
            async def segment_decoder():
                """Continuously decode segments without blocking fetching"""
                try:
                    segment_count = 0

                    while session.status == 'active':
                        try:
                            # Get raw segment data (wait up to 100ms)
                            segment_data = await asyncio.wait_for(
                                raw_segment_queue.get(),
                                timeout=0.1
                            )

                            if segment_data is None:
                                logger.info("End of segments signal received")
                                break

                            # Decode segment to frames (this runs in parallel with fetching)
                            frames = decoder.process_segment(segment_data)

                            # Buffer decoded frames - PRESERVE ALL FRAMES
                            frames_buffered = 0
                            for frame in frames:
                                await decoded_frames_buffer.put(frame)
                                frames_buffered += 1

                            if frames_buffered > 0:
                                logger.debug(f"Decoded {frames_buffered} frames from segment {segment_count}")

                            segment_count += 1
                            if segment_count % 10 == 0:
                                logger.info(f"Decoded {segment_count} segments (pipeline)")

                        except asyncio.TimeoutError:
                            continue  # No segments available, keep trying
                        except Exception as e:
                            logger.error(f"Error in segment decoder: {e}")
                            await asyncio.sleep(0.005)

                    # Signal end of frames
                    await decoded_frames_buffer.put(None)
                    logger.info("Segment decoder finished")

                except Exception as e:
                    logger.error(f"Segment decoder error: {e}")

            # TASK 3: FRAME QUEUE MANAGER - Continuously feed main queue
            async def frame_queue_manager():
                """Continuously move frames from buffer to main queue"""
                try:
                    frames_forwarded = 0

                    while session.status == 'active':
                        try:
                            # Get decoded frame from buffer (wait up to 50ms)
                            frame = await asyncio.wait_for(
                                decoded_frames_buffer.get(),
                                timeout=0.05
                            )

                            if frame is None:
                                logger.info("End of frames signal received")
                                break

                            # Forward to main decoded frame queue - PRESERVE ALL FRAMES
                            if session.decoded_frame_queue:
                                await session.decoded_frame_queue.put(frame)
                                frames_forwarded += 1

                                if frames_forwarded % 100 == 0:
                                    logger.debug(f"Forwarded {frames_forwarded} frames to WebRTC")

                        except asyncio.TimeoutError:
                            continue  # No frames available, keep trying
                        except Exception as e:
                            logger.error(f"Error in frame queue manager: {e}")
                            await asyncio.sleep(0.005)

                    logger.info(f"Frame queue manager finished ({frames_forwarded} total frames)")

                except Exception as e:
                    logger.error(f"Frame queue manager error: {e}")

            # RUN PIPELINE TASKS CONCURRENTLY
            pipeline_tasks = await asyncio.gather(
                asyncio.create_task(segment_fetcher()),
                asyncio.create_task(segment_decoder()),
                asyncio.create_task(frame_queue_manager()),
                return_exceptions=True
            )

            logger.info("Trickle subscriber pipeline finished")

        except Exception as e:
            logger.error(f"Trickle subscriber pipeline error: {e}")

    async def _run_webrtc_processing(self, session: RelaySession):
        """Handle WebRTC connection to ComfyStream"""
        try:
            logger.info("Starting WebRTC processing")

            # Ensure queues are initialized
            assert session.decoded_frame_queue is not None, "Decoded frame queue not initialized"
            assert session.processed_frame_queue is not None, "Processed frame queue not initialized"

            # Create video track for sending frames
            input_track = VideoStreamTrack(session.decoded_frame_queue)

            # Create WebRTC client and connection
            webrtc_client = WebRTCClient(session.comfystream_url)

            try:
                pc, data_channel = await webrtc_client.create_connection(
                    session.prompts,
                    session.width,
                    session.height,
                    input_track,
                    session.processed_frame_queue
                )

                session.peer_connection = pc
                session.data_channel = data_channel

                # Monitor connection state
                @pc.on("connectionstatechange")
                async def on_connection_state_change():
                    logger.info(f"WebRTC state: {pc.connectionState}")
                    if pc.connectionState in ["failed", "closed"]:
                        input_track.stop()

                                # Keep connection alive (OPTIMIZED: faster monitoring)
                while (session.status == 'active' and
                       pc.connectionState not in ["failed", "closed"]):
                    await asyncio.sleep(0.01)  # ULTRA-OPTIMIZED: 10ms instead of 100ms

            finally:
                await webrtc_client.close()
                input_track.stop()

        except Exception as e:
            logger.error(f"WebRTC processing error: {e}")

    async def _run_trickle_publisher(self, session: RelaySession):
        """Publish processed frames as trickle segments to port 3389"""
        from trickle_components import TrickleSegmentEncoder, enhanced_segment_publisher

        try:
            logger.info(f"Starting ACTUAL trickle publisher for {session.output_stream_url}")

            # Ensure queues are initialized
            assert session.processed_frame_queue is not None, "Processed frame queue not initialized"
            assert session.output_segment_queue is not None, "Output segment queue not initialized"

            # Create segment encoder for processed frames
            encoder = TrickleSegmentEncoder(
                width=512,  # Standard output size
                height=512,
                fps=24,
                format="mpegts",
                video_codec="libx264"
            )

            # Create concurrent tasks for frame encoding and segment publishing
            async def frame_encoder():
                """Encode processed frames from ComfyStream into trickle segments with consistent timing"""
                try:
                    # Ensure queues are available in this scope
                    assert session.processed_frame_queue is not None
                    assert session.output_segment_queue is not None

                    frames_processed = 0
                    segment_duration = 1.0  # 1 second segments
                    target_fps = 24.0
                    frames_per_segment = int(target_fps * segment_duration)  # 24 frames

                    # Global timestamp tracking for continuous PTS/DTS across segments
                    global_frame_count = 0  # Continuous frame counter across all segments
                    last_frame = None  # For frame repetition when processing is slow
                    segment_start_time = time.time()
                    current_segment_frames = []
                    segment_count = 0

                    logger.info(f"Starting time-based encoder: {frames_per_segment} frames per {segment_duration}s segment")

                    while session.status == 'active':
                        try:
                            # Calculate time-based segment boundaries
                            current_time = time.time()
                            segment_elapsed = current_time - segment_start_time

                            # Try to get new processed frame (non-blocking)
                            new_frame = None
                            try:
                                new_frame = await asyncio.wait_for(
                                    session.processed_frame_queue.get(),
                                    timeout=0.1  # Short timeout for responsive segmentation
                                )

                                if new_frame is None:
                                    # End of stream - encode final segment
                                    if current_segment_frames:
                                        segment_data = encoder.encode_frames(current_segment_frames)
                                        if segment_data:
                                            await session.output_segment_queue.put(segment_data)
                                            logger.info(f"Encoded final segment {segment_count} with {len(current_segment_frames)} frames")
                                    break

                                # Got new frame - update tracking
                                last_frame = new_frame
                                frames_processed += 1

                            except asyncio.TimeoutError:
                                # No new frame available - that's OK for time-based encoding
                                pass

                            # Time to create a segment? (every 1 second)
                            if segment_elapsed >= segment_duration:
                                # Ensure we have frames for this segment
                                if not current_segment_frames and last_frame is not None:
                                    # Fill segment with repeated frames if no frames accumulated
                                    logger.debug(f"Padding segment {segment_count} with repeated frames (slow AI processing)")
                                    current_segment_frames = [last_frame] * frames_per_segment
                                elif len(current_segment_frames) < frames_per_segment and last_frame is not None:
                                    # Pad segment to target frame count with repeated frames
                                    frames_needed = frames_per_segment - len(current_segment_frames)
                                    current_segment_frames.extend([last_frame] * frames_needed)
                                    logger.debug(f"Padded segment {segment_count} with {frames_needed} repeated frames")
                                elif len(current_segment_frames) > frames_per_segment:
                                    # Trim excess frames
                                    current_segment_frames = current_segment_frames[:frames_per_segment]
                                    logger.debug(f"Trimmed segment {segment_count} to {frames_per_segment} frames")

                                # Encode segment with PRESERVED timing to maintain H.264 frame dependencies
                                if current_segment_frames:
                                    # CRITICAL: Do NOT modify PTS/time_base if frames already have proper timing
                                    # Changing timing can break H.264 reference frame dependencies
                                    frames_with_timing = 0
                                    for frame in current_segment_frames:
                                        if frame.pts is not None and frame.time_base is not None:
                                            frames_with_timing += 1

                                    # Only set timing if frames don't already have it
                                    if frames_with_timing == 0:
                                        logger.debug("Setting sequential timing for frames without timestamps")
                                        for i, frame in enumerate(current_segment_frames):
                                            frame.pts = global_frame_count + i
                                            frame.time_base = Fraction(1, int(target_fps))
                                    else:
                                        logger.debug(f"Preserving existing timing for {frames_with_timing}/{len(current_segment_frames)} frames")

                                    # Update global frame counter for next segment
                                    global_frame_count += len(current_segment_frames)

                                    segment_data = encoder.encode_frames(current_segment_frames)
                                    if segment_data:
                                        # Queue segment for publishing - NO DROPPING
                                        await session.output_segment_queue.put(segment_data)
                                        logger.info(f"Encoded H.264-safe segment {segment_count}: {len(current_segment_frames)} frames, {len(segment_data)} bytes")

                                # Reset for next segment
                                segment_count += 1
                                current_segment_frames = []
                                segment_start_time = current_time

                                if frames_processed % 100 == 0 and frames_processed > 0:
                                    logger.info(f"Processed {frames_processed} frames into {segment_count} H.264-compliant segments")

                            # Add new frame to current segment if we got one
                            if new_frame is not None:
                                current_segment_frames.append(new_frame)

                            # Small sleep to prevent busy waiting
                            await asyncio.sleep(0.01)

                        except Exception as e:
                            logger.error(f"Error in time-based encoding: {e}")
                            await asyncio.sleep(0.1)

                    # Signal end of segments
                    await session.output_segment_queue.put(None)
                    logger.info(f"Time-based encoder finished: {frames_processed} frames → {segment_count} segments")

                except Exception as e:
                    logger.error(f"Frame encoder error: {e}")

            async def segment_publisher():
                """Publish encoded segments to trickle server on port 3389"""
                try:
                    # Ensure queue is available
                    assert session.output_segment_queue is not None

                    # Start enhanced segment publisher pointing to trickle server
                    await enhanced_segment_publisher(
                        session.output_stream_url,  # Output stream URL
                        session.output_segment_queue,
                        target_fps=24.0,
                        segment_duration=1.0  # 1 second segments (24 frames each)
                    )
                    logger.info("Segment publisher finished")

                except Exception as e:
                    logger.error(f"Segment publisher error: {e}")

            # Run frame encoding and segment publishing concurrently
            publishing_tasks = await asyncio.gather(
                asyncio.create_task(frame_encoder()),
                asyncio.create_task(segment_publisher()),
                return_exceptions=True
            )

            logger.info("Trickle publisher pipeline finished")

        except Exception as e:
            logger.error(f"Trickle publisher error: {e}")

    async def stop_session(self, session_id: str):
        """Stop a relay session"""
        try:
            if session_id not in self.active_sessions:
                raise ValueError(f"Session {session_id} not found")

            session = self.active_sessions[session_id]
            session.status = 'stopping'

            logger.info(f"Stopping relay session {session_id}")

            # Cancel tasks
            tasks = [
                session.subscriber_task,
                session.webrtc_task,
                session.publisher_task
            ]

            for task in tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            # Close WebRTC connection
            if session.peer_connection:
                await session.peer_connection.close()

            # Signal end of queues
            for queue in [session.decoded_frame_queue,
                         session.processed_frame_queue,
                         session.output_segment_queue]:
                if queue:
                    try:
                        await queue.put(None)
                    except:
                        pass

            session.status = 'stopped'
            logger.info(f"Stopped relay session {session_id}")

        except Exception as e:
            logger.error(f"Error stopping session {session_id}: {e}")
            raise

    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a session with enhanced monitoring"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]

        # Calculate processing rates
        decoded_queue_size = session.decoded_frame_queue.qsize() if session.decoded_frame_queue else 0
        processed_queue_size = session.processed_frame_queue.qsize() if session.processed_frame_queue else 0
        output_queue_size = session.output_segment_queue.qsize() if session.output_segment_queue else 0

        return {
            'session_id': session.session_id,
            'status': session.status,
            'input_stream_url': session.input_stream_url,
            'output_stream_url': session.output_stream_url,
            'comfystream_url': session.comfystream_url,
            'width': session.width,
            'height': session.height,
            'created_at': session.created_at.isoformat(),
            'queue_sizes': {
                'decoded_frames': decoded_queue_size,
                'processed_frames': processed_queue_size,
                'output_segments': output_queue_size
            },
            'queue_capacity': {
                'decoded_frames': 'unlimited',
                'processed_frames': 'unlimited',
                'output_segments': 'unlimited'
            },
            'processing_health': {
                'input_to_webrtc_backlog': decoded_queue_size,
                'webrtc_to_output_backlog': processed_queue_size,
                'output_publishing_backlog': output_queue_size,
                'bottleneck': self._identify_bottleneck(decoded_queue_size, processed_queue_size, output_queue_size)
            },
            'webrtc_state': session.peer_connection.connectionState if session.peer_connection else 'disconnected'
        }

    def _identify_bottleneck(self, decoded_size: int, processed_size: int, output_size: int) -> str:
        """Identify processing bottleneck based on queue sizes"""
        if decoded_size > 1000:
            return "ComfyStream WebRTC processing slow"
        elif processed_size > 1000:
            return "Output encoding/publishing slow"
        elif output_size > 100:
            return "Trickle server publishing slow"
        else:
            return "No bottleneck detected"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions"""
        return [
            {
                'session_id': session.session_id,
                'status': session.status,
                'input_stream_url': session.input_stream_url,
                'output_stream_url': session.output_stream_url,
                'created_at': session.created_at.isoformat()
            }
            for session in self.active_sessions.values()
        ]

# HTTP API handlers
async def start_session_handler(request):
    """HTTP handler to start a relay session"""
    try:
        data = await request.json()
        relay_service = request.app['relay_service']

        session_id = await relay_service.start_session(data)

        # Get the session to retrieve the output stream URL
        session = relay_service.active_sessions[session_id]

        from aiohttp.web import json_response
        return json_response({
            'success': True,
            'session_id': session_id,
            'input_stream_url': session.input_stream_url,
            'output_stream_url': session.output_stream_url,
            'comfystream_url': session.comfystream_url
        })

    except Exception as e:
        logger.error(f"Error in start session handler: {e}")
        from aiohttp.web import json_response
        return json_response(
            {'error': str(e)},
            status=500
        )

async def stop_session_handler(request):
    """HTTP handler to stop a relay session"""
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']

        await relay_service.stop_session(session_id)

        from aiohttp.web import json_response
        return json_response({
            'success': True,
            'message': f'Session {session_id} stopped'
        })

    except Exception as e:
        logger.error(f"Error in stop session handler: {e}")
        from aiohttp.web import json_response
        return json_response(
            {'error': str(e)},
            status=500
        )

async def get_session_status_handler(request):
    """HTTP handler to get session status"""
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']

        status = await relay_service.get_session_status(session_id)

        from aiohttp.web import json_response
        return json_response({
            'success': True,
            'session': status
        })

    except Exception as e:
        logger.error(f"Error in session status handler: {e}")
        from aiohttp.web import json_response
        return json_response(
            {'error': str(e)},
            status=500
        )

async def list_sessions_handler(request):
    """HTTP handler to list sessions"""
    try:
        relay_service = request.app['relay_service']
        sessions = relay_service.list_sessions()

        from aiohttp.web import json_response
        return json_response({
            'success': True,
            'sessions': sessions
        })

    except Exception as e:
        logger.error(f"Error in list sessions handler: {e}")
        from aiohttp.web import json_response
        return json_response(
            {'error': str(e)},
            status=500
        )

async def health_handler(request):
    """Health check handler"""
    from aiohttp.web import json_response
    return json_response({'status': 'healthy'})

async def create_app(relay_service: TrickleRelayService):
    """Create aiohttp application"""
    import aiohttp.web as web
    try:
        from aiohttp_cors import setup as setup_cors, ResourceOptions
        cors_available = True
    except ImportError:
        cors_available = False
        logger.warning("aiohttp_cors not available - CORS will not be enabled")

    app = web.Application()
    app['relay_service'] = relay_service

    # Setup CORS if available
    if cors_available:
        cors = setup_cors(app, defaults={
            "*": ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"]
            )
        })

        # Add routes with CORS
        cors.add(app.router.add_post('/session/start', start_session_handler))
        cors.add(app.router.add_delete('/session/{session_id}', stop_session_handler))
        cors.add(app.router.add_get('/session/{session_id}/status', get_session_status_handler))
        cors.add(app.router.add_get('/sessions', list_sessions_handler))
        cors.add(app.router.add_get('/health', health_handler))
    else:
        # Add routes without CORS
        app.router.add_post('/session/start', start_session_handler)
        app.router.add_delete('/session/{session_id}', stop_session_handler)
        app.router.add_get('/session/{session_id}/status', get_session_status_handler)
        app.router.add_get('/sessions', list_sessions_handler)
        app.router.add_get('/health', health_handler)

    return app

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trickle Relay Service")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", default=8890, type=int, help="Bind port")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Log level")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    async def main():
        relay_service = TrickleRelayService(args.host, args.port)
        app = await create_app(relay_service)

        logger.info(f"🚀 Trickle Relay Service starting on {args.host}:{args.port}")
        logger.info("📡 Endpoints available:")
        logger.info(f"  - Start session: POST http://{args.host}:{args.port}/session/start")
        logger.info(f"  - Stop session: DELETE http://{args.host}:{args.port}/session/{{session_id}}")
        logger.info(f"  - Session status: GET http://{args.host}:{args.port}/session/{{session_id}}/status")
        logger.info(f"  - List sessions: GET http://{args.host}:{args.port}/sessions")
        logger.info(f"  - Health check: GET http://{args.host}:{args.port}/health")

        import aiohttp.web as web
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        logger.info("✅ Service started successfully!")
        logger.info("📋 To start a relay session, POST to /session/start with:")
        logger.info("   {")
        logger.info("     \"input_stream_url\": \"http://example.com/input\",")
        logger.info("     \"comfystream_url\": \"http://localhost:8889\",")
        logger.info("     \"prompts\": [{\"text\": \"your prompt here\"}],")
        logger.info("     \"width\": 512,")
        logger.info("     \"height\": 512")
        logger.info("   }")

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            await runner.cleanup()

    asyncio.run(main())
