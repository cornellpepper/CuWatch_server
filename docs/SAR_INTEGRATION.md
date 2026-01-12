# SAR Integration Documentation

## Overview

The CuWatch system health monitoring page displays historical CPU, memory, and disk I/O data by parsing **SAR (System Activity Report)** data files collected by the `sysstat` package. This provides up to 30 days of historical metrics without requiring any additional collection infrastructure.

## Architecture

### Data Flow
1. **sysstat daemon** (runs on host) → collects metrics every 10 minutes → stores in `/var/log/sysstat/`
2. **Web container** (reads via volume mount) → uses `sar` command to parse binary data files
3. **SARParser** (Python module) → converts SAR output to JSON
4. **API endpoints** → `/api/system/sar/{cpu,memory,disk}` return time-series data
5. **Frontend** → Plotly charts visualize the data

### Key Components

**[services/web/sar_parser.py](services/web/sar_parser.py)**
- Parses SAR binary files using the `sar` command
- Returns data in standardized format with ISO8601 timestamps
- Methods: `get_cpu_history()`, `get_memory_history()`, `get_disk_io_history()`

**[services/web/app.py](services/web/app.py)**
- API endpoints: `/api/system/sar/cpu`, `/api/system/sar/memory`, `/api/system/sar/disk`
- Query parameter: `days_back` (0 = today, 1 = yesterday, etc.)

**[docker-compose.yml](docker-compose.yml)**
- Volume mount: `-/var/log/sysstat:/var/log/sysstat:ro` (read-only)

**[services/web/Dockerfile](services/web/Dockerfile)**
- Installs `sysstat` package for `sar` command

## System Dependencies

### Raspbian (Current)
```bash
# Package manager
apt-get install sysstat

# Data location
/var/log/sysstat/sa[DD]  # DD = day of month (01-31)

# Command
sar -f /var/log/sysstat/sa12 -u  # CPU data
sar -f /var/log/sysstat/sa12 -r  # Memory data
sar -f /var/log/sysstat/sa12 -b  # Disk I/O data
```

### Ubuntu / Debian
**No changes needed!** Ubuntu uses the same:
- `apt-get install sysstat`
- `/var/log/sysstat/` directory
- `sar` command syntax

### CentOS / RHEL
```bash
# Package manager
yum install sysstat

# Data location (may be different)
/var/log/sa/sa[DD]  # Check your system

# Command (same)
sar -f /var/log/sa/sa12 -u
```

### Alpine Linux
```bash
# Package manager
apk add sysstat

# Data location
/var/log/sysstat/sa[DD]

# Command (same)
sar -f /var/log/sysstat/sa12 -u
```

### macOS
```bash
# Package manager
brew install sysstat

# Data location (may be different)
/var/log/sysstat/ or custom location

# Command (same)
sar -f /var/log/sysstat/sa12 -u
```

## How to Migrate to Another System

### 1. Update Dockerfile
**Current (Raspbian):**
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends curl sysstat
```

**For CentOS/RHEL:**
```dockerfile
RUN yum install -y curl sysstat
```

**For Alpine:**
```dockerfile
RUN apk add --no-cache curl sysstat
```

### 2. Verify SAR Data Path
Check where `sysstat` stores data on your new system:
```bash
# Find SAR data files
find /var -name "sa[0-9][0-9]" 2>/dev/null

# Or check sysstat config
cat /etc/sysstat/sysstat.conf  # or similar
```

### 3. Update docker-compose.yml
If SAR path is different, update the volume mount:
```yaml
# Current (Raspbian/Ubuntu/Debian)
volumes:
  - /var/log/sysstat:/var/log/sysstat:ro

# Alternative (some CentOS)
volumes:
  - /var/log/sa:/var/log/sysstat:ro
```

### 4. Update sar_parser.py
If path changed, update the `SAR_DIR` constant:
```python
# Current
SAR_DIR = Path('/var/log/sysstat')

# Alternative
SAR_DIR = Path('/var/log/sa')
```

### 5. Test
```bash
# Verify SAR command works in container
docker exec <container> sar -f /var/log/sysstat/sa$(date +%d) -u

# Check API endpoint
curl http://localhost/api/system/sar/cpu?days_back=0
```

## Key Assumptions & Limitations

✅ **Works across systems** - Linux systems running sysstat  
⚠️ **Path may vary** - CentOS/RHEL often use `/var/log/sa/` instead of `/var/log/sysstat/`  
⚠️ **Collection must be running** - sysstat daemon (`sysstat-collect.service` or `sa-collect.timer`) must be active  
⚠️ **10-minute intervals** - Data is sampled every 10 minutes; finer granularity not available  
⚠️ **Retention** - sysstat typically keeps 28 days of data (configurable)  

## Enabling sysstat on Fresh Install

### Raspbian/Ubuntu/Debian
```bash
sudo apt-get install sysstat
sudo systemctl enable sysstat
sudo systemctl start sysstat
```

### CentOS/RHEL
```bash
sudo yum install sysstat
sudo systemctl enable sysstat
sudo systemctl start sysstat
```

### Alpine
```bash
apk add sysstat
# Note: Alpine may require manual setup; check their docs
```

## Code Entry Points

**If you need to modify the integration:**

1. **Change data source** → Update `SAR_DIR` in [sar_parser.py](../../services/web/sar_parser.py#L11)
2. **Change collection interval** → Modify `days_back` parameter in API calls or sysstat config
3. **Add new metrics** → Add method to `SARParser` class (e.g., `get_network_history()`)
4. **Change API structure** → Modify routes in [app.py](../../services/web/app.py) around line 362-398
5. **Change chart layout** → Update JavaScript in [system_health.html](../../services/web/templates/system_health.html) around line 295

## Alternative: No SAR Dependency

If you want to avoid SAR entirely and use application-level collection instead:

1. Remove `sysstat` from Dockerfile
2. Delete [sar_parser.py](../../services/web/sar_parser.py)
3. Revert docker-compose.yml volume mount
4. Use the original `SystemMonitor` class (from before SAR integration) for in-app collection
5. Trade-off: Only keeps 24 hours of data, adds background collection overhead

## References

- [sysstat Documentation](https://github.com/sysstat/sysstat)
- [SAR Man Page](https://linux.die.net/man/1/sar)
- System-specific docs:
  - Raspbian: Debian-based, use apt
  - Ubuntu: Debian-based, use apt
  - CentOS: Use yum/dnf
  - Alpine: Use apk
  - macOS: Use brew

