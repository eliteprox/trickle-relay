#!/bin/bash
# Test WHEP Setup Script
#
# This script demonstrates the complete WHEP testing workflow:
# 1. Start WHIP server with video file
# 2. Test the WHEP client against it

set -e

# Configuration
VIDEO_FILE="/home/elite/repos/trickle-relay/bbb_sunflower_1080p_30fps_normal.mp4"
WHIP_SERVER_HOST="localhost"
WHIP_SERVER_PORT="8080"
WHEP_ENDPOINT="http://${WHIP_SERVER_HOST}:${WHIP_SERVER_PORT}/whep/stream/default"
COMFYSTREAM_URL="http://localhost:8889"

echo "🚀 WHEP Testing Setup"
echo "====================="
echo "📹 Video file: $VIDEO_FILE"
echo "📡 WHIP server: http://${WHIP_SERVER_HOST}:${WHIP_SERVER_PORT}"
echo "📺 WHEP endpoint: $WHEP_ENDPOINT"
echo ""

# Check if video file exists
if [ ! -f "$VIDEO_FILE" ]; then
    echo "❌ Error: Video file not found: $VIDEO_FILE"
    exit 1
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source whep_env/bin/activate

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "🧹 Cleaning up..."
    if [ ! -z "$WHIP_SERVER_PID" ]; then
        echo "Stopping WHIP server (PID: $WHIP_SERVER_PID)..."
        kill $WHIP_SERVER_PID 2>/dev/null || true
    fi
    echo "👋 Cleanup complete"
}

# Set trap for cleanup
trap cleanup EXIT

# Check what the user wants to do
echo "Choose an option:"
echo "1. Start WHIP server only (for manual testing)"
echo "2. Start WHIP server and run WHEP client"
echo "3. Just show example commands"
read -p "Enter choice (1-3): " choice

case $choice in
    1)
        echo ""
        echo "🎬 Starting WHIP server with video file..."
        python3 simple_whip_server.py \
            --video-file "$VIDEO_FILE" \
            --host "$WHIP_SERVER_HOST" \
            --port "$WHIP_SERVER_PORT" \
            --log-level INFO
        ;;

    2)
        echo ""
        echo "🎬 Starting WHIP server with video file..."
        python3 simple_whip_server.py \
            --video-file "$VIDEO_FILE" \
            --host "$WHIP_SERVER_HOST" \
            --port "$WHIP_SERVER_PORT" \
            --log-level INFO &

        WHIP_SERVER_PID=$!
        echo "WHIP server started with PID: $WHIP_SERVER_PID"

        # Wait for server to start
        echo "⏳ Waiting for WHIP server to start..."
        sleep 3

        # Test if server is running
        if curl -s "http://${WHIP_SERVER_HOST}:${WHIP_SERVER_PORT}/status" > /dev/null; then
            echo "✅ WHIP server is running"
        else
            echo "❌ WHIP server failed to start"
            exit 1
        fi

        echo ""
        echo "🔗 Starting WHEP client..."
        python3 whep_to_comfystream_relay.py \
            --whep-endpoint "$WHEP_ENDPOINT" \
            --comfystream-url "$COMFYSTREAM_URL" \
            --prompt "enhance this test video stream" \
            --log-level INFO
        ;;

    3)
        echo ""
        echo "📋 Example Commands:"
        echo ""
        echo "1. Start WHIP server:"
        echo "   source whep_env/bin/activate"
        echo "   python3 simple_whip_server.py \\"
        echo "       --video-file '$VIDEO_FILE' \\"
        echo "       --host '$WHIP_SERVER_HOST' \\"
        echo "       --port '$WHIP_SERVER_PORT'"
        echo ""
        echo "2. In another terminal, start WHEP client:"
        echo "   source whep_env/bin/activate"
        echo "   python3 whep_to_comfystream_relay.py \\"
        echo "       --whep-endpoint '$WHEP_ENDPOINT' \\"
        echo "       --prompt 'enhance this test video'"
        echo ""
        echo "3. Check server status:"
        echo "   curl http://${WHIP_SERVER_HOST}:${WHIP_SERVER_PORT}/status"
        echo ""
        echo "4. Alternative WHEP endpoints:"
        echo "   http://${WHIP_SERVER_HOST}:${WHIP_SERVER_PORT}/whep/stream/default"
        echo ""
        ;;

    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
