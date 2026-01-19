#!/bin/bash
set -e

# Database rotation script for CuWatch Server
# Rotates the database to a new semester/period-specific volume
# Based on instructions from README.md

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
BACKUP=true
BIND_MOUNT=false
NEW_VOLUME_NAME=""

usage() {
    cat << EOF
Usage: $0 <volume_name> [OPTIONS]

Rotate the database to a new volume for a fresh start while preserving old data.

Arguments:
  volume_name       Name for the new database (e.g., spring2026, fall2025)
                    For named volumes: will be prefixed with project name
                    For bind mounts: will be ./data/postgres_<volume_name>

Options:
  --no-backup      Skip backing up the current database
  --bind-mount     Use bind mount instead of named volume (./data/postgres_<name>)
  -h, --help       Show this help message

Examples:
  # Create a new named volume for spring 2026
  $0 spring2026

  # Create a new bind mount without backup
  $0 spring2026 --bind-mount --no-backup

  # Rotate to fall 2025 with automatic backup
  $0 fall2025
EOF
}

# Parse options first
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        --no-backup)
            BACKUP=false
            shift
            ;;
        --bind-mount)
            BIND_MOUNT=true
            shift
            ;;
        -*)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
        *)
            # First non-option argument is the volume name
            if [[ -z "$NEW_VOLUME_NAME" ]]; then
                NEW_VOLUME_NAME="$1"
                shift
            else
                print_error "Unexpected argument: $1"
                usage
                exit 1
            fi
            ;;
    esac
done

# Check that volume name was provided
if [[ -z "$NEW_VOLUME_NAME" ]]; then
    print_error "Volume name is required"
    usage
    exit 1
fi

# Validate volume name
if [[ ! "$NEW_VOLUME_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    print_error "Volume name must contain only letters, numbers, underscores, and hyphens"
    exit 1
fi

print_info "Starting database rotation to: $NEW_VOLUME_NAME"

# Check if stack is running
STACK_RUNNING=false
if docker compose ps | grep -q "Up"; then
    STACK_RUNNING=true
fi

# Show what will happen
echo ""
echo "════════════════════════════════════════════════════════════"
echo "PREVIEW: What will happen next"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Current state:"
if [ "$STACK_RUNNING" = true ]; then
    echo "  • Stack is RUNNING"
    if [ "$BACKUP" = true ]; then
        echo "  • Current database will be BACKED UP to: backup_YYYYMMDD_HHMMSS.sql"
    else
        echo "  • Current database will NOT be backed up"
    fi
else
    echo "  • Stack is NOT running"
    echo "  • Backup will be SKIPPED"
fi
echo ""
echo "Actions:"
echo "  1. Stop the Docker Compose stack"
if [ "$BIND_MOUNT" = true ]; then
    echo "  2. Create new directory: ./data/postgres_${NEW_VOLUME_NAME}"
    echo "  3. Update docker-compose.override.yml to use bind mount"
else
    echo "  2. Update docker-compose.override.yml to use new named volume: pgdata_${NEW_VOLUME_NAME}"
fi
echo "  3. Start stack with fresh database"
echo "  4. Wait for database to initialize"
echo ""
echo "Result:"
if [ "$BIND_MOUNT" = true ]; then
    echo "  • Old data location: ./data/postgres_pgdata (if using default)"
    echo "  • New data location: ./data/postgres_${NEW_VOLUME_NAME}"
else
    echo "  • Old volume: pgdata (accessible via 'docker volume ls')"
    echo "  • New volume: pgdata_${NEW_VOLUME_NAME}"
fi
echo "  • Database will be fresh/empty"
echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Ask for confirmation
read -p "Proceed with database rotation? (yes/no): " -r CONFIRMATION
if [[ ! "$CONFIRMATION" =~ ^[Yy][Ee][Ss]$ ]]; then
    print_info "Rotation cancelled by user"
    exit 0
fi

echo ""
if ! [ "$STACK_RUNNING" = true ]; then
    print_warning "Stack does not appear to be running"
fi

# Step 1: Backup current database (if requested and stack is running)
if [ "$BACKUP" = true ]; then
    BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).sql"
    print_info "Creating backup: $BACKUP_FILE"
    
    if docker compose ps db | grep -q "Up"; then
        if docker compose exec -T db pg_dump -U postgres -d iot > "$BACKUP_FILE" 2>/dev/null; then
            BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
            print_info "Backup created successfully ($BACKUP_SIZE)"
        else
            print_warning "Could not create backup (database may be empty or not running)"
            rm -f "$BACKUP_FILE"
        fi
    else
        print_warning "Database container not running, skipping backup"
    fi
fi

# Step 2: Stop the stack
print_info "Stopping the stack..."
docker compose down

# Step 3: Update docker-compose.override.yml
OVERRIDE_FILE="docker-compose.override.yml"

if [ "$BIND_MOUNT" = true ]; then
    # Use bind mount
    NEW_VOLUME_PATH="./data/postgres_${NEW_VOLUME_NAME}"
    
    print_info "Creating directory: $NEW_VOLUME_PATH"
    mkdir -p "$NEW_VOLUME_PATH"
    
    print_info "Updating $OVERRIDE_FILE for bind mount..."
    cat > "$OVERRIDE_FILE" << EOF
services:
  db:
    volumes:
      - ${NEW_VOLUME_PATH}:/var/lib/postgresql/data

  bridge:
    environment:
      # Debug logging is OFF by default; enable by exporting BRIDGE_DEBUG=1
      BRIDGE_DEBUG: \${BRIDGE_DEBUG:-0}
EOF
    
    print_info "Database will use bind mount: $NEW_VOLUME_PATH"
else
    # Use named volume
    PROJECT_NAME=$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/_/g')
    FULL_VOLUME_NAME="${PROJECT_NAME}_pgdata_${NEW_VOLUME_NAME}"
    
    print_info "Updating $OVERRIDE_FILE for named volume..."
    cat > "$OVERRIDE_FILE" << EOF
services:
  db:
    volumes:
      - pgdata_${NEW_VOLUME_NAME}:/var/lib/postgresql/data

  bridge:
    environment:
      # Debug logging is OFF by default; enable by exporting BRIDGE_DEBUG=1
      BRIDGE_DEBUG: \${BRIDGE_DEBUG:-0}

volumes:
  pgdata_${NEW_VOLUME_NAME}:
EOF
    
    print_info "Database will use named volume: $FULL_VOLUME_NAME"
fi

# Step 4: Start the stack with new volume
print_info "Starting stack with new database volume..."
docker compose up -d --build

# Step 5: Wait for database to be healthy
print_info "Waiting for database to be ready..."
RETRIES=30
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $RETRIES ]; do
    if docker compose exec -T db pg_isready -U postgres -d iot > /dev/null 2>&1; then
        print_info "Database is ready!"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 1
done

if [ $RETRY_COUNT -eq $RETRIES ]; then
    print_error "Database failed to become ready after ${RETRIES} seconds"
    exit 1
fi

# Step 6: Show summary
echo ""
print_info "Database rotation complete!"
echo ""
echo "Summary:"
echo "  New volume: $NEW_VOLUME_NAME"
if [ "$BIND_MOUNT" = true ]; then
    echo "  Type: Bind mount"
    echo "  Location: $(pwd)/$NEW_VOLUME_PATH"
else
    echo "  Type: Named volume"
    echo "  Full name: $FULL_VOLUME_NAME"
    echo "  Inspect: docker volume inspect $FULL_VOLUME_NAME"
fi
echo ""

# Show old volumes for reference
print_info "Available database volumes:"
if [ "$BIND_MOUNT" = true ]; then
    ls -lhd data/postgres_* 2>/dev/null || echo "  (no previous bind mounts found)"
else
    docker volume ls | grep pgdata || echo "  (no named volumes found)"
fi

echo ""
print_info "Stack status:"
docker compose ps
