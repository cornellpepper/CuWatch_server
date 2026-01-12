# SAR (System Activity Report) data parser for Raspbian
import subprocess
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


class SARParser:
    """Parse SAR data files from /var/log/sysstat"""
    
    SAR_DIR = Path('/var/log/sysstat')
    
    @staticmethod
    def get_cpu_history(days_back=1):
        """
        Get historical CPU usage data.
        days_back: number of days to look back (default 1 = today)
        Returns list of {ts, user, nice, system, iowait, idle} dicts
        """
        data = []
        
        # Get date for SAR file
        target_date = datetime.now() - timedelta(days=days_back)
        sar_file = SARParser.SAR_DIR / f"sa{target_date.strftime('%d')}"
        
        if not sar_file.exists():
            return data
        
        try:
            # Run sar command to extract CPU data
            result = subprocess.run(
                ['sar', '-f', str(sar_file), '-u'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return data
            
            # Parse output
            lines = result.stdout.split('\n')
            date_str = target_date.strftime('%Y-%m-%d')
            
            for line in lines:
                # Skip headers and empty lines
                if 'CPU' in line or 'all' not in line or not line.strip():
                    continue
                
                # Parse line: TIME CPU %user %nice %system %iowait %steal %idle
                parts = line.split()
                if len(parts) < 8:
                    continue
                
                try:
                    time_str = parts[0]  # HH:MM:SS
                    user = float(parts[2])
                    system = float(parts[4])
                    iowait = float(parts[5])
                    
                    # Create timestamp
                    ts = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M:%S')
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                    # Calculate overall CPU usage (100 - idle)
                    idle = float(parts[7])
                    overall = 100.0 - idle
                    
                    data.append({
                        'ts': ts.isoformat(),
                        'overall': round(overall, 1),
                        'user': round(user, 1),
                        'system': round(system, 1),
                        'iowait': round(iowait, 1),
                        'idle': round(idle, 1),
                    })
                except (ValueError, IndexError):
                    continue
        
        except Exception as e:
            print(f"Error parsing SAR CPU data: {e}")
        
        return data
    
    @staticmethod
    def get_memory_history(days_back=1):
        """
        Get historical memory usage data.
        days_back: number of days to look back (default 1 = today)
        Returns list of {ts, kbmemfree, kbmemused, memused_percent} dicts
        """
        data = []
        
        # Get date for SAR file
        target_date = datetime.now() - timedelta(days=days_back)
        sar_file = SARParser.SAR_DIR / f"sa{target_date.strftime('%d')}"
        
        if not sar_file.exists():
            return data
        
        try:
            # Run sar command to extract memory data
            result = subprocess.run(
                ['sar', '-f', str(sar_file), '-r'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return data
            
            lines = result.stdout.split('\n')
            date_str = target_date.strftime('%Y-%m-%d')
            
            for line in lines:
                # Skip headers and empty lines
                if 'kbmemfree' in line or not line.strip():
                    continue
                
                parts = line.split()
                if len(parts) < 5:
                    continue
                
                try:
                    # Format: TIME kbmemfree kbavail kbmemused %memused ...
                    time_str = parts[0]
                    kbmemfree = float(parts[1])
                    # kbavail = parts[2]  # skip
                    kbmemused = float(parts[3])
                    memused_percent = float(parts[4])
                    
                    ts = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M:%S')
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                    data.append({
                        'ts': ts.isoformat(),
                        'kbmemfree': int(kbmemfree),
                        'kbmemused': int(kbmemused),
                        'memused_percent': round(memused_percent, 1),
                    })
                except (ValueError, IndexError):
                    continue
        
        except Exception as e:
            print(f"Error parsing SAR memory data: {e}")
        
        return data
    
    @staticmethod
    def get_disk_io_history(days_back=1):
        """
        Get historical disk I/O data.
        days_back: number of days to look back (default 1 = today)
        Returns list of {ts, tps, read_kb_s, write_kb_s} dicts
        """
        data = []
        
        # Get date for SAR file
        target_date = datetime.now() - timedelta(days=days_back)
        sar_file = SARParser.SAR_DIR / f"sa{target_date.strftime('%d')}"
        
        if not sar_file.exists():
            return data
        
        try:
            # Run sar command to extract disk I/O data
            result = subprocess.run(
                ['sar', '-f', str(sar_file), '-b'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return data
            
            lines = result.stdout.split('\n')
            date_str = target_date.strftime('%Y-%m-%d')
            
            for line in lines:
                # Skip headers and empty lines
                if 'tps' in line or not line.strip():
                    continue
                
                parts = line.split()
                if len(parts) < 4:
                    continue
                
                try:
                    time_str = parts[0]
                    tps = float(parts[1])
                    read_kb_s = float(parts[2])
                    write_kb_s = float(parts[3])
                    
                    ts = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M:%S')
                    ts = ts.replace(tzinfo=timezone.utc)
                    
                    # Convert KB/s to MB/s
                    read_mb_s = read_kb_s / 1024
                    write_mb_s = write_kb_s / 1024
                    
                    data.append({
                        'ts': ts.isoformat(),
                        'tps': round(tps, 2),
                        'read_mb_s': round(read_mb_s, 2),
                        'write_mb_s': round(write_mb_s, 2),
                    })
                except (ValueError, IndexError):
                    continue
        
        except Exception as e:
            print(f"Error parsing SAR disk I/O data: {e}")
        
        return data
    
    @staticmethod
    def get_available_dates():
        """Get list of dates with available SAR data"""
        dates = []
        try:
            for f in sorted(SARParser.SAR_DIR.glob('sa[0-9][0-9]')):
                match = re.match(r'sa(\d{2})', f.name)
                if match:
                    day = int(match.group(1))
                    dates.append(day)
        except Exception:
            pass
        return sorted(set(dates), reverse=True)
