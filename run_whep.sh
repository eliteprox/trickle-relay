#!/bin/bash
# WHEP Client Runner Script

# Activate virtual environment
source whep_env/bin/activate

# Set default values
WHEP_ENDPOINT=${1:-"http://localhost:7088/whip/endpoint/test"}
COMFYSTREAM_URL=${2:-"http://localhost:8889"}
PROMPT=${3:-"enhance this video stream"}
BEARER_TOKEN=${4:-""}

echo "🚀 Starting WHEP to ComfyStream Relay"
echo "📡 WHEP Endpoint: $WHEP_ENDPOINT"
echo "🧠 ComfyStream: $COMFYSTREAM_URL"
echo "💭 Prompt: $PROMPT"
echo ""

# Build command
CMD="python3 whep_to_comfystream_relay.py --whep-endpoint '$WHEP_ENDPOINT' --comfystream-url '$COMFYSTREAM_URL' --prompt '$PROMPT'"

if [ ! -z "$BEARER_TOKEN" ]; then
    CMD="$CMD --bearer-token '$BEARER_TOKEN'"
fi

echo "Running: $CMD"
echo ""

# Execute
eval $CMD
