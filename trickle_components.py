"""
Standalone Trickle HTTP Protocol Components

Self-contained implementation of trickle HTTP streaming protocol
for the relay service. Based on ComfyStream patterns but independent.
"""

import asyncio
import aiohttp
import logging
import av
import io
import time
from typing import Optional, List, AsyncIterator, Dict, Any
from fractions import Fraction

logger = logging.getLogger(__name__)

class TrickleSubscriber:
    """
    Trickle HTTP subscriber following the trickle protocol.
    Subscribes to trickle streams using GET requests.
    """

    def __init__(self, url: str):
        self.url = url
        self.session = None
        self.segment_idx = None
        self.discovered = False

    async def __aenter__(self):
        """Enter context manager"""
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(verify_ssl=False),
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
        )
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Exit context manager"""
        await self.close()

    async def next(self) -> Optional['SegmentReader']:
        """Get next segment from trickle stream"""
        try:
            if self.session is None:
                logger.error("Session is None, cannot subscribe")
                return None

            # First time: discover latest segment using -1 protocol
            if not self.discovered:
                return await self._discover_latest_segment()

            if self.segment_idx is None:
                logger.error("Segment index not set after discovery")
                return None

            # Build segment URL: baseurl/segment_index
            segment_url = f"{self.url}/{self.segment_idx}"
            logger.debug(f"Requesting segment: {segment_url}")

            response = await self.session.get(segment_url)

            if response.status == 200:
                logger.debug(f"Got segment {self.segment_idx}")
                current_idx = self.segment_idx
                self.segment_idx += 1
                return SegmentReader(response, current_idx)
            elif response.status == 404:
                # Segment not available yet
                logger.debug(f"Segment {self.segment_idx} not ready")
                response.close()
                return None
            elif response.status == 470:
                # Segment too old - re-discover
                logger.warning(f"Segment {self.segment_idx} too old, re-discovering")
                response.close()
                self.discovered = False
                self.segment_idx = None
                return await self._discover_latest_segment()
            else:
                logger.error(f"Failed to get segment {segment_url}: {response.status}")
                response.close()
                return None

        except asyncio.TimeoutError:
            logger.debug(f"Timeout waiting for segment {self.segment_idx}")
            return None
        except Exception as e:
            logger.error(f"Error getting segment: {e}")
            return None

    async def _discover_latest_segment(self) -> Optional['SegmentReader']:
        """Discover latest segment using -1 protocol"""
        try:
            if self.session is None:
                return None

            # Use -1 to get most recent segment
            discovery_url = f"{self.url}/-1"
            logger.info(f"Discovering latest segment: {discovery_url}")

            response = await self.session.get(discovery_url)

            if response.status == 200:
                logger.info("Discovered latest segment")
                self.discovered = True

                # Get sequence number from header
                lp_trickle_seq = response.headers.get('Lp-Trickle-Seq')

                if lp_trickle_seq:
                    try:
                        current_idx = int(lp_trickle_seq)
                        self.segment_idx = current_idx + 1
                        logger.info(f"Next segment will be {self.segment_idx}")
                        return SegmentReader(response, current_idx)
                    except ValueError:
                        logger.warning(f"Invalid Lp-Trickle-Seq: {lp_trickle_seq}")
                        self.segment_idx = 1
                        return SegmentReader(response, -1)
                else:
                    # No header, start from 1
                    self.segment_idx = 1
                    logger.info(f"Starting from segment {self.segment_idx}")
                    return SegmentReader(response, -1)

            elif response.status == 404:
                logger.warning("No segments available")
                response.close()
                return None
            else:
                logger.error(f"Discovery failed: {response.status}")
                response.close()
                return None

        except Exception as e:
            logger.error(f"Discovery error: {e}")
            return None

    async def close(self):
        """Close session"""
        if self.session:
            await self.session.close()
            self.session = None

class SegmentReader:
    """Reader for trickle segment data"""

    def __init__(self, response, segment_idx: int = 0):
        self.response = response
        self.segment_idx = segment_idx
        self._closed = False
        self._total_read = 0

    async def read(self, size: int = 8192) -> bytes:
        """Read data from segment"""
        if self._closed:
            return b""

        try:
            data = await self.response.content.read(size)

            if data:
                self._total_read += len(data)
                return data
            else:
                # End of segment
                logger.debug(f"Segment {self.segment_idx} complete: {self._total_read} bytes")
                self._closed = True
                return b""

        except Exception as e:
            logger.error(f"Error reading segment {self.segment_idx}: {e}")
            self._closed = True
            return b""

    async def close(self):
        """Close segment reader"""
        if self.response and not self._closed:
            self.response.close()
            self._closed = True

class TricklePublisher:
    """
    Trickle HTTP publisher for publishing segments
    """

    def __init__(self, url: str, mime_type: str = "video/mp2t", start_idx: int = 0):
        self.url = url
        self.mime_type = mime_type
        self.idx = start_idx
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(verify_ssl=False)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    async def publish_segment(self, segment_data: bytes) -> bool:
        """Publish segment data to trickle stream"""
        try:
            if not isinstance(segment_data, bytes):
                raise TypeError("segment_data must be bytes")

            segment_url = f"{self.url}/{self.idx}"
            logger.debug(f"Publishing segment to {segment_url}")

            async with self.session.post(
                segment_url,
                data=segment_data,
                headers={
                    'Content-Type': self.mime_type,
                    'Lp-Trickle-Seq': str(self.idx)
                }
            ) as response:

                if response.status == 200:
                    logger.debug(f"Published segment {self.idx}")
                    self.idx += 1
                    return True
                else:
                    logger.error(f"Failed to publish segment {self.idx}: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error publishing segment {self.idx}: {e}")
            return False

    async def close(self):
        """Close publisher"""
        if self.session:
            await self.session.close()

class TrickleStreamDecoder:
    """
    Decoder for trickle segments to video frames
    """

    def __init__(self, target_width: Optional[int] = None, target_height: Optional[int] = None):
        # CRITICAL: Set to None to disable resizing and preserve H.264 integrity
        self.target_width = target_width
        self.target_height = target_height
        self.total_frames = 0

    def process_segment(self, segment_data: bytes) -> List[av.VideoFrame]:
        """Process trickle segment into video frames - PRESERVE H.264 NAL UNIT INTEGRITY"""
        try:
            if not segment_data:
                return []

            # Open segment with PyAV
            input_buffer = io.BytesIO(segment_data)
            container = av.open(input_buffer, mode='r')

            frames = []

            # Find video stream
            if not container.streams.video:
                logger.warning("No video stream in segment")
                container.close()
                input_buffer.close()
                return []

            video_stream = container.streams.video[0]
            logger.debug(f"Processing segment: {video_stream.width}x{video_stream.height}")

            # CRITICAL: Decode frames WITHOUT any format conversions to preserve H.264 NAL units
            for packet in container.demux(video_stream):
                for frame in packet.decode():
                    # Only process video frames to avoid attribute errors
                    if isinstance(frame, av.VideoFrame):
                        # ABSOLUTELY NO FORMAT CONVERSIONS - preserve original frame format and timing
                        # Any modifications can corrupt H.264 NAL unit structure and cause decoder errors

                        # Preserve original timing information from packet
                        if hasattr(packet, 'pts') and packet.pts is not None:
                            frame.pts = packet.pts
                        if hasattr(packet, 'time_base') and packet.time_base is not None:
                            frame.time_base = packet.time_base

                        frames.append(frame)
                        self.total_frames += 1

            container.close()
            input_buffer.close()

            logger.debug(f"Decoded {len(frames)} frames from segment with preserved H.264 integrity")
            return frames

        except Exception as e:
            logger.error(f"Error decoding segment: {e}")
            return []

class TrickleSegmentEncoder:
    """
    Encoder for video frames to trickle segments
    """

    def __init__(self, width: int = 512, height: int = 512, fps: int = 24,
                 format: str = "mpegts", video_codec: str = "libx264"):
        self.width = width
        self.height = height
        self.fps = fps
        self.format = format
        self.video_codec = video_codec
        self.segment_count = 0

    def encode_frames(self, frames: List[av.VideoFrame]) -> bytes:
        """Encode frames into trickle segment with H.264 NAL unit preservation"""
        try:
            if not frames:
                return b""

            # Create output buffer
            output_buffer = io.BytesIO()

            # Create container with proper H.264 settings for trickle streaming
            container = av.open(output_buffer, mode='w', format=self.format)

            # Add video stream with H.264 NAL unit friendly settings
            video_stream = container.add_stream(self.video_codec, rate=self.fps)

            # Ensure we have a video stream, not subtitle stream
            if video_stream.type != 'video':
                logger.error(f"Expected video stream, got {video_stream.type}")
                container.close()
                output_buffer.close()
                return b""

            video_stream.width = self.width
            video_stream.height = self.height
            video_stream.pix_fmt = 'yuv420p'

            # CRITICAL: Set codec options for proper H.264 NAL unit structure
            video_stream.codec_context.options = {
                'preset': 'ultrafast',           # Faster encoding, less compression artifacts
                'tune': 'zerolatency',           # Minimize buffering for live streaming
                'profile': 'baseline',          # Ensure compatibility
                'level': '3.1',                 # Standard level for 512x512
                'keyint': str(self.fps),        # Keyframe every second for segment boundaries
                'keyint_min': '1',              # Allow more frequent keyframes
                'x264-params': 'nal-hrd=cbr:force-cfr=1',  # Proper NAL unit structure
                'repeat_headers': '1',          # Include SPS/PPS in each segment
                'annex_b': '1'                  # Use Annex B NAL unit format
            }

            # Encode frames with proper timing and H.264 structure
            for i, frame in enumerate(frames):
                # Ensure we're processing video frames
                if not isinstance(frame, av.VideoFrame):
                    logger.warning(f"Skipping non-video frame: {type(frame)}")
                    continue

                # MINIMAL format conversion - only if absolutely necessary
                if (frame.format.name != 'yuv420p' or
                    frame.width != self.width or
                    frame.height != self.height):

                    logger.debug(f"Converting frame from {frame.format.name} {frame.width}x{frame.height} to yuv420p {self.width}x{self.height}")
                    frame = frame.reformat(
                        format='yuv420p',
                        width=self.width,
                        height=self.height
                    )

                # Preserve original timing if available, otherwise use sequential timing
                if frame.pts is not None and frame.time_base is not None:
                    # Keep original timing to maintain H.264 frame dependencies
                    pass
                else:
                    # Set sequential timing for proper H.264 stream structure
                    frame.pts = i
                    frame.time_base = Fraction(1, self.fps)

                # Encode with proper H.264 NAL unit structure
                packets = video_stream.encode(frame)
                for packet in packets:
                    # Ensure proper NAL unit boundaries
                    if packet.size > 0:
                        container.mux(packet)

            # Flush encoder to ensure all NAL units are written
            packets = video_stream.encode()
            for packet in packets:
                if packet.size > 0:
                    container.mux(packet)

            container.close()

            # Get encoded data
            segment_data = output_buffer.getvalue()
            output_buffer.close()

            self.segment_count += 1
            logger.debug(f"Encoded {len(frames)} frames to {len(segment_data)} bytes with proper H.264 NAL units")

            return segment_data

        except Exception as e:
            logger.error(f"Error encoding frames: {e}")
            return b""

    def encode_single_frame(self, frame: av.VideoFrame) -> bytes:
        """Encode single frame into segment"""
        return self.encode_frames([frame])

async def enhanced_segment_publisher(base_url: str, segment_queue: asyncio.Queue,
                                   target_fps: float = 24.0,
                                   segment_duration: float = 3.0) -> None:
    """
    Enhanced segment publisher with timing control
    """
    try:
        logger.info(f"Starting enhanced publisher for {base_url}")

        async with TricklePublisher(base_url) as publisher:
            segment_count = 0
            segment_interval = segment_duration
            last_publish_time = None

            while True:
                try:
                    # Get segment data
                    segment_data = await asyncio.wait_for(
                        segment_queue.get(),
                        timeout=5.0
                    )

                    if segment_data is None:
                        logger.info("End of stream")
                        break

                    # Handle tuple format (segment_id, data)
                    if isinstance(segment_data, tuple):
                        segment_id, data = segment_data
                        segment_data = data

                    if not isinstance(segment_data, bytes):
                        logger.error(f"Expected bytes, got {type(segment_data)}")
                        continue

                    # Timing control
                    current_time = time.time()
                    if last_publish_time is not None:
                        time_since_last = current_time - last_publish_time
                        if time_since_last < segment_interval:
                            wait_time = segment_interval - time_since_last
                            await asyncio.sleep(wait_time)
                            current_time = time.time()

                    # Publish segment
                    success = await publisher.publish_segment(segment_data)
                    if success:
                        segment_count += 1
                        last_publish_time = current_time

                        if segment_count % 10 == 0:
                            logger.info(f"Published {segment_count} segments")

                except asyncio.TimeoutError:
                    logger.debug("No segments to publish")
                    continue
                except Exception as e:
                    logger.error(f"Error in publisher: {e}")
                    break

        logger.info(f"Publisher finished: {segment_count} segments")

    except Exception as e:
        logger.error(f"Publisher error: {e}")
