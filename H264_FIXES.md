# H.264 Frame Processing Fixes for Trickle Relay

## Problem Summary
The ComfyStream WebRTC side was experiencing H.264 decoder errors when receiving streams from the trickle relay:
- "No start code is found"
- "Error splitting the input into NAL units"
- "Missing reference picture" errors
- "illegal short term buffer state detected"

## Root Cause Analysis
These errors were caused by:
1. **Format Conversions**: Unnecessary frame reformatting in the decoder corrupted H.264 NAL unit structure
2. **Timing Issues**: Improper PTS/DTS assignments broke H.264 frame dependencies
3. **NAL Unit Corruption**: Multiple reformat operations destroyed H.264 encoded data integrity

## Fixes Implemented

### 1. TrickleStreamDecoder (trickle_components.py)
**Key Changes:**
- Removed all unnecessary format conversions that corrupt H.264 NAL units
- Preserved original frame format, dimensions, and timing from source
- Added proper video frame type checking to avoid processing errors
- Maintained packet timing information (PTS/DTS) to preserve frame dependencies

**Before:**
```python
# WRONG: This corrupted H.264 NAL units
if frame.width != self.target_width:
    frame = frame.reformat(width=self.target_width, height=self.target_height)
```

**After:**
```python
# CORRECT: Preserve original H.264 integrity
if isinstance(frame, av.VideoFrame):
    # ABSOLUTELY NO FORMAT CONVERSIONS - preserve original frame format and timing
    # Preserve original timing information from packet
    if hasattr(packet, 'pts') and packet.pts is not None:
        frame.pts = packet.pts
    if hasattr(packet, 'time_base') and packet.time_base is not None:
        frame.time_base = packet.time_base
```

### 2. VideoStreamTrack (trickle_relay_service.py)
**Key Changes:**
- Implemented true passthrough mode with zero frame modifications
- Added detailed logging to track H.264 frame integrity
- Ensured no timing or format changes that could corrupt NAL units

**Before:**
```python
# This claimed passthrough but frames could already be corrupted
return frame
```

**After:**
```python
# ABSOLUTE PASSTHROUGH: Do NOT modify ANYTHING about the frame
# No format changes, no timing changes, no property modifications
# Any changes can corrupt H.264 NAL unit structure
logger.debug(f"H.264 passthrough frame: {frame.width}x{frame.height}, format={frame.format.name}, pts={frame.pts}, time_base={frame.time_base}")
return frame
```

### 3. TrickleSegmentEncoder (trickle_components.py)
**Key Changes:**
- Enhanced H.264 codec options for proper NAL unit structure
- Added NAL unit boundary checking
- Preserved original timing when available to maintain frame dependencies
- Added proper H.264 baseline profile settings

**Key Settings:**
```python
video_stream.codec_context.options = {
    'preset': 'ultrafast',           # Faster encoding, less artifacts
    'tune': 'zerolatency',           # Minimize buffering for live streaming
    'profile': 'baseline',          # Ensure compatibility
    'level': '3.1',                 # Standard level for 512x512
    'keyint': str(self.fps),        # Keyframe every second for segments
    'keyint_min': '1',              # Allow more frequent keyframes
    'x264-params': 'nal-hrd=cbr:force-cfr=1',  # Proper NAL unit structure
    'repeat_headers': '1',          # Include SPS/PPS in each segment
    'annex_b': '1'                  # Use Annex B NAL unit format
}
```

### 4. Frame Timing Preservation (trickle_relay_service.py)
**Key Changes:**
- Avoided overriding existing PTS/time_base values that maintain H.264 dependencies
- Only set timing for frames without existing timestamps
- Preserved H.264 frame reference relationships

**Before:**
```python
# WRONG: This broke H.264 frame dependencies
frame.pts = global_frame_count + i
frame.time_base = Fraction(1, int(target_fps))
```

**After:**
```python
# CORRECT: Preserve existing timing to maintain H.264 dependencies
frames_with_timing = sum(1 for frame in current_segment_frames
                        if frame.pts is not None and frame.time_base is not None)
if frames_with_timing == 0:
    # Only set timing if frames don't already have it
    frame.pts = global_frame_count + i
    frame.time_base = Fraction(1, int(target_fps))
else:
    logger.debug(f"Preserving existing timing for {frames_with_timing} frames")
```

## Testing & Validation

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run with Debug Logging
```bash
python3 trickle_relay_service.py --log-level DEBUG
```

### 3. Monitor for H.264 Errors
Look for these log messages indicating success:
- "Decoded X frames from segment with preserved H.264 integrity"
- "H.264 passthrough frame: ..."
- "Encoded X frames to X bytes with proper H.264 NAL units"

### 4. ComfyStream Side Validation
The following errors should NO LONGER appear in ComfyStream logs:
- ❌ "No start code is found"
- ❌ "Error splitting the input into NAL units"
- ❌ "Missing reference picture"
- ❌ "illegal short term buffer state detected"

## Expected Results
With these fixes, the trickle relay should:
1. ✅ Preserve H.264 NAL unit structure throughout the pipeline
2. ✅ Maintain proper frame dependencies and timing
3. ✅ Send clean H.264 streams to ComfyStream via WebRTC
4. ✅ Eliminate decoder errors on the ComfyStream side
5. ✅ Provide stable video processing at the correct FPS

## Monitoring Commands
```bash
# Check relay service status
curl http://localhost:8890/sessions

# Monitor H.264 stream integrity
tail -f /var/log/trickle-relay.log | grep "H.264\|NAL\|passthrough"

# Verify WebRTC connection
curl http://localhost:8890/sessions/<session_id>/status
```
