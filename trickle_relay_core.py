#!/usr/bin/env python3
"""
Trickle Relay Service - Core Implementation

A standalone microservice that acts as a bridge between trickle HTTP segments 
and WebRTC connections to ComfyStream. This service:

1. Ingests trickle HTTP segments from input streams
2. Decodes segments to video frames  
3. Establishes WebRTC connection to ComfyStream
4. Sends frames via WebRTC for AI processing
5. Receives processed frames via WebRTC
6. Encodes processed frames back to trickle segments
7. Publishes output trickle stream
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

# WebRTC imports (using same library as ComfyStream)
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
    MediaStreamTrack
)

# Import trickle components from ComfyStream
# Note: These imports assume the ComfyStream package is available
try:
    from comfystream.server.trickle import (
        TrickleSubscriber,
        TricklePublisher, 
        TrickleStreamDecoder,
        TrickleSegmentEncoder,
        enhanced_segment_publisher
    )
    TRICKLE_AVAILABLE = True
except ImportError:
    TRICKLE_AVAILABLE = False
    logging.warning("ComfyStream trickle components not available - using fallback implementations")

logger = logging.getLogger(__name__)

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
    status: str = 'starting'  # starting, active, stopping, stopped
    created_at: datetime = None
    
    # Processing components
    subscriber_task: Optional[asyncio.Task] = None
    webrtc_task: Optional[asyncio.Task] = None
    publisher_task: Optional[asyncio.Task] = None
    
    # Data queues for frame processing pipeline
    decoded_frame_queue: Optional[asyncio.Queue] = None
    processed_frame_queue: Optional[asyncio.Queue] = None
    output_segment_queue: Optional[asyncio.Queue] = None
    
    # WebRTC components
    peer_connection: Optional[RTCPeerConnection] = None
    data_channel: Optional[Any] = None
    
    # Processing components
    decoder: Optional[Any] = None
    encoder: Optional[Any] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

class VideoStreamTrack(MediaStreamTrack):
    """Custom video stream track that sends decoded frames via WebRTC"""
    
    kind = "video"
    
    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True
        
    async def recv(self):
        """Send next frame via WebRTC"""
        try:
            if not self._running:
                raise Exception("Track stopped")
                
            # Get next frame from queue with timeout
            frame = await asyncio.wait_for(
                self.frame_queue.get(),
                timeout=5.0
            )
            
            if frame is None:
                # End of stream signal
                self._running = False
                raise Exception("End of stream")
                
            return frame
            
        except asyncio.TimeoutError:
            # Generate a blank frame to keep WebRTC alive
            blank_frame = av.VideoFrame.from_rgb24(
                bytes([0] * (512 * 512 * 3)), 
                width=512, 
                height=512
            )
            return blank_frame
            
    def stop(self):
        """Stop the track"""
        self._running = False

class ProcessedVideoReceiver:
    """Handles receiving processed frames from ComfyStream via WebRTC"""
    
    def __init__(self, output_queue: asyncio.Queue):
        self.output_queue = output_queue
        self._running = True
        
    async def handle_frame(self, frame):
        """Handle received processed frame"""
        try:
            if self._running and self.output_queue:
                await self.output_queue.put(frame)
                logger.debug(f"Received processed frame: {frame.width}x{frame.height}")
        except Exception as e:
            logger.error(f"Error handling processed frame: {e}")
            
    def stop(self):
        """Stop the receiver"""
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
            # Create peer connection
            pc = RTCPeerConnection()
            
            # Add input track (our decoded frames)
            pc.addTrack(input_track)
            
            # Create data channel for control messages
            data_channel = pc.createDataChannel("control")
            
            # Set up receiver for processed frames
            frame_receiver = ProcessedVideoReceiver(output_queue)
            
            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track from ComfyStream: {track.kind}")
                if track.kind == "video":
                    # Set up frame handling
                    async def process_incoming_frames():
                        try:
                            while frame_receiver._running:
                                frame = await track.recv()
                                await frame_receiver.handle_frame(frame)
                        except Exception as e:
                            logger.error(f"Error processing incoming frames: {e}")
                    
                    # Start processing incoming frames
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
        try:
            session_id = session_config.get('session_id', str(uuid.uuid4()))
            
            # Validate required fields
            required_fields = ['input_stream_url', 'comfystream_url', 'prompts']
            for field in required_fields:
                if field not in session_config:
                    raise ValueError(f"Missing required field: {field}")
            
            # Create session
            session = RelaySession(
                session_id=session_id,
                input_stream_url=session_config['input_stream_url'],
                output_stream_url=session_config.get('output_stream_url', 
                                                   f"{session_config['input_stream_url']}-processed"),
                comfystream_url=session_config['comfystream_url'],
                prompts=session_config['prompts'],
                width=session_config.get('width', 512),
                height=session_config.get('height', 512)
            )
            
            # Initialize processing queues
            session.decoded_frame_queue = asyncio.Queue(maxsize=100)
            session.processed_frame_queue = asyncio.Queue(maxsize=100)  
            session.output_segment_queue = asyncio.Queue(maxsize=50)
            
            # Initialize processing components
            if TRICKLE_AVAILABLE:
                session.decoder = TrickleStreamDecoder(
                    target_width=session.width,
                    target_height=session.height
                )
                
                session.encoder = TrickleSegmentEncoder(
                    width=session.width,
                    height=session.height,
                    fps=24,
                    format="mpegts",
                    video_codec="libx264"
                )
            else:
                logger.warning("Using fallback implementations - limited functionality")
                session.decoder = None
                session.encoder = None
            
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
            logger.info(f"  Input: {session.input_stream_url}")
            logger.info(f"  Output: {session.output_stream_url}")
            logger.info(f"  ComfyStream: {session.comfystream_url}")
            
            return session_id
            
        except Exception as e:
            logger.error(f"Error starting session: {e}")
            raise
            
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
            
    async def _run_trickle_subscriber(self, session: RelaySession):
        """Run trickle subscriber for input stream"""
        try:
            logger.info(f"Starting trickle subscriber for {session.input_stream_url}")
            
            if not TRICKLE_AVAILABLE or not session.decoder:
                logger.error("Trickle components not available")
                return
                
            async with TrickleSubscriber(session.input_stream_url) as subscriber:
                segment_count = 0
                consecutive_empty = 0
                max_consecutive_empty = 50
                
                while session.status == 'active':
                    try:
                        # Get next segment
                        segment_reader = await subscriber.next()
                        if segment_reader is None:
                            consecutive_empty += 1
                            if consecutive_empty >= max_consecutive_empty:
                                logger.info("No more segments available, ending subscriber")
                                break
                            await asyncio.sleep(0.1)
                            continue
                            
                        consecutive_empty = 0
                        
                        # Read segment data
                        segment_data = b""
                        while True:
                            chunk = await segment_reader.read(8192)
                            if not chunk:
                                break
                            segment_data += chunk
                            
                        await segment_reader.close()
                        
                        if segment_data:
                            # Decode segment to frames
                            frames = session.decoder.process_segment(segment_data)
                            
                            # Queue frames for WebRTC processing
                            for frame in frames:
                                await session.decoded_frame_queue.put(frame)
                                
                            segment_count += 1
                            if segment_count % 10 == 0:
                                logger.info(f"Processed {segment_count} input segments")
                                
                    except Exception as e:
                        logger.error(f"Error in trickle subscriber: {e}")
                        await asyncio.sleep(0.5)
                        
            logger.info(f"Trickle subscriber finished for session {session.session_id}")
                        
        except Exception as e:
            logger.error(f"Trickle subscriber error: {e}")
            
    async def _run_webrtc_processing(self, session: RelaySession):
        """Run WebRTC processing pipeline"""
        try:
            logger.info("Starting WebRTC processing")
            
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
                
                # Monitor WebRTC connection state
                @pc.on("connectionstatechange")
                async def on_connection_state_change():
                    logger.info(f"WebRTC connection state: {pc.connectionState}")
                    if pc.connectionState in ["failed", "closed"]:
                        input_track.stop()
                
                # Keep connection alive and process frames
                frame_count = 0
                while session.status == 'active' and pc.connectionState not in ["failed", "closed"]:
                    # Check if we have processed frames to encode
                    try:
                        processed_frame = await asyncio.wait_for(
                            session.processed_frame_queue.get(),
                            timeout=1.0
                        )
                        
                        if processed_frame is None:
                            break
                            
                        # Encode processed frame to segment
                        if session.encoder:
                            segment_data = session.encoder.encode_frame(processed_frame, frame_count)
                            if segment_data:
                                await session.output_segment_queue.put(segment_data)
                                frame_count += 1
                                
                    except asyncio.TimeoutError:
                        # No processed frames available yet
                        continue
                    except Exception as e:
                        logger.error(f"Error processing WebRTC frame: {e}")
                        
            finally:
                await webrtc_client.close()
                input_track.stop()
                
        except Exception as e:
            logger.error(f"WebRTC processing error: {e}")
            
    async def _run_trickle_publisher(self, session: RelaySession):
        """Run trickle publisher for output stream"""
        try:
            logger.info(f"Starting trickle publisher for {session.output_stream_url}")
            
            if not TRICKLE_AVAILABLE:
                logger.error("Trickle publisher components not available")
                return
            
            # Start enhanced segment publisher
            await enhanced_segment_publisher(
                session.output_stream_url,
                session.output_segment_queue,
                add_metadata_headers=True,
                target_fps=24.0,
                segment_duration=3.0
            )
            
        except Exception as e:
            logger.error(f"Trickle publisher error: {e}")
            
    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a session"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")
            
        session = self.active_sessions[session_id]
        
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
                'decoded_frames': session.decoded_frame_queue.qsize() if session.decoded_frame_queue else 0,
                'processed_frames': session.processed_frame_queue.qsize() if session.processed_frame_queue else 0,
                'output_segments': session.output_segment_queue.qsize() if session.output_segment_queue else 0
            },
            'webrtc_state': session.peer_connection.connectionState if session.peer_connection else 'disconnected'
        }
        
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