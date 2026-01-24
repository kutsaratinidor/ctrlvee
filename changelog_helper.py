"""Helper module to parse and format CHANGELOG.md for Discord display"""
import os
import re
from typing import List, Dict, Tuple


def parse_changelog(max_versions: int = 2) -> List[Dict]:
    """
    Parse CHANGELOG.md and return the most recent version entries.
    
    Args:
        max_versions: Maximum number of version entries to return
        
    Returns:
        List of dicts with keys: version, date, sections (dict of section->items)
    """
    changelog_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'CHANGELOG.md')
    if not os.path.exists(changelog_path):
        return []
    
    with open(changelog_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split by "## X.Y.Z" pattern
    version_blocks = re.split(r'\n## ', '\n' + content)
    
    results = []
    for block in version_blocks[1:max_versions + 1]:  # Skip first empty split
        lines = block.split('\n')
        version_line = lines[0].strip()
        
        # Parse version and date
        version_match = re.match(r'([\d.]+)(?:\s*-\s*(.+))?', version_line)
        if not version_match:
            continue
        
        version = version_match.group(1)
        date_str = version_match.group(2) or "Unknown"
        
        # Collect changes by section
        sections = {}
        current_section = None
        
        for line in lines[1:]:
            line_stripped = line.strip()
            # Stop at next version (## marker)
            if line_stripped.startswith('## '):
                break
            
            if line_stripped.startswith('###'):
                # Section header: "### Changed", "### Added", "### Fixed"
                current_section = line_stripped.replace('###', '').strip()
                sections[current_section] = []
            elif line_stripped.startswith('- ') and current_section:
                # Bullet point: "- description"
                sections[current_section].append(line_stripped[2:])
            # Skip empty lines; don't break on them
        
        results.append({
            'version': version,
            'date': date_str,
            'sections': sections
        })
    
    return results
