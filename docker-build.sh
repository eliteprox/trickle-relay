#!/bin/bash

# Trickle Relay Service - Docker Build Script
# This script provides easy commands to build, run, and manage the Docker container

set -e

# Configuration
IMAGE_NAME="trickle-relay"
CONTAINER_NAME="trickle-relay-service"
PORT="8890"
VERSION="latest"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Show help
show_help() {
    echo "Trickle Relay Service - Docker Management Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  build       Build the Docker image"
    echo "  run         Run the container"
    echo "  stop        Stop the container"
    echo "  restart     Restart the container"
    echo "  logs        Show container logs"
    echo "  shell       Open shell in running container"
    echo "  health      Check container health"
    echo "  clean       Remove container and image"
    echo "  compose-up  Start using docker-compose"
    echo "  compose-down Stop docker-compose services"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 build"
    echo "  $0 run"
    echo "  $0 logs -f"
    echo ""
}

# Build Docker image
build_image() {
    log_info "Building Docker image: ${IMAGE_NAME}:${VERSION}"

    if ! docker build -t "${IMAGE_NAME}:${VERSION}" -t "${IMAGE_NAME}:latest" .; then
        log_error "Failed to build Docker image"
        exit 1
    fi

    log_success "Docker image built successfully"

    # Show image info
    docker images | grep "${IMAGE_NAME}" | head -5
}

# Run container
run_container() {
    log_info "Running container: ${CONTAINER_NAME}"

    # Stop existing container if running
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        log_warning "Container ${CONTAINER_NAME} is already running. Stopping it first..."
        docker stop "${CONTAINER_NAME}" || true
        docker rm "${CONTAINER_NAME}" || true
    fi

    # Create logs and data directories
    mkdir -p logs data

    # Run new container
    docker run -d \
        --name "${CONTAINER_NAME}" \
        -p "${PORT}:${PORT}" \
        -v "$(pwd)/logs:/app/logs" \
        -v "$(pwd)/data:/app/data" \
        --restart unless-stopped \
        "${IMAGE_NAME}:${VERSION}" \
        "$@"

    log_success "Container started successfully"
    log_info "Service available at: http://localhost:${PORT}"
    log_info "Health check: http://localhost:${PORT}/health"
}

# Stop container
stop_container() {
    log_info "Stopping container: ${CONTAINER_NAME}"

    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker stop "${CONTAINER_NAME}"
        log_success "Container stopped"
    else
        log_warning "Container ${CONTAINER_NAME} is not running"
    fi
}

# Restart container
restart_container() {
    log_info "Restarting container: ${CONTAINER_NAME}"
    stop_container
    sleep 2
    run_container "$@"
}

# Show logs
show_logs() {
    log_info "Showing logs for: ${CONTAINER_NAME}"

    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker logs "${CONTAINER_NAME}" "$@"
    else
        log_error "Container ${CONTAINER_NAME} is not running"
        exit 1
    fi
}

# Open shell in container
open_shell() {
    log_info "Opening shell in: ${CONTAINER_NAME}"

    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker exec -it "${CONTAINER_NAME}" /bin/bash
    else
        log_error "Container ${CONTAINER_NAME} is not running"
        exit 1
    fi
}

# Check health
check_health() {
    log_info "Checking health of: ${CONTAINER_NAME}"

    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        # Check Docker health status
        health_status=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_NAME}" 2>/dev/null || echo "no-healthcheck")
        log_info "Docker health status: ${health_status}"

        # Check HTTP endpoint
        if curl -f -s "http://localhost:${PORT}/health" > /dev/null; then
            log_success "Service is healthy and responding"
        else
            log_warning "Service is not responding to health checks"
        fi

        # Show container status
        docker ps --filter name="${CONTAINER_NAME}"
    else
        log_error "Container ${CONTAINER_NAME} is not running"
        exit 1
    fi
}

# Clean up
clean_up() {
    log_info "Cleaning up Docker resources"

    # Stop and remove container
    if docker ps -aq -f name="${CONTAINER_NAME}" | grep -q .; then
        log_info "Removing container: ${CONTAINER_NAME}"
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    fi

    # Remove image
    if docker images -q "${IMAGE_NAME}" | grep -q .; then
        log_info "Removing image: ${IMAGE_NAME}"
        docker rmi "${IMAGE_NAME}:${VERSION}" 2>/dev/null || true
        docker rmi "${IMAGE_NAME}:latest" 2>/dev/null || true
    fi

    log_success "Cleanup completed"
}

# Docker Compose commands
compose_up() {
    log_info "Starting services with docker-compose"

    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose is not installed"
        exit 1
    fi

    docker-compose up -d "$@"
    log_success "Services started with docker-compose"
}

compose_down() {
    log_info "Stopping services with docker-compose"

    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose is not installed"
        exit 1
    fi

    docker-compose down "$@"
    log_success "Services stopped with docker-compose"
}

# Main script logic
case "${1}" in
    build)
        build_image
        ;;
    run)
        shift
        run_container "$@"
        ;;
    stop)
        stop_container
        ;;
    restart)
        shift
        restart_container "$@"
        ;;
    logs)
        shift
        show_logs "$@"
        ;;
    shell)
        open_shell
        ;;
    health)
        check_health
        ;;
    clean)
        clean_up
        ;;
    compose-up)
        shift
        compose_up "$@"
        ;;
    compose-down)
        shift
        compose_down "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    "")
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
