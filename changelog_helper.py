"""Helper module to parse and format CHANGELOG.md for Discord display"""
import os
import re
from typing import List, Dict


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


def whats_new(version: str, max_items: int = 4) -> List[Dict]:
    """This version's Added/Changed/Fixed bullets as {title, section, slash, prefix} for a short summary.
    title is the bold lead-in ("**Movie roulette**: ..."); slash/prefix are the first
    `/...` and `!...` commands an Added bullet names (None otherwise). Bullets without a
    bold lead-in are skipped; empty if the version isn't logged."""
    entry = next((e for e in parse_changelog(max_versions=10_000) if e['version'] == version), None)
    if not entry:
        return []
    items = []
    for section in ('Added', 'Changed', 'Fixed'):
        for text in entry['sections'].get(section, []):
            match = re.match(r'\*\*(.+?)\*\*', text)
            if not match:
                continue
            commands = re.findall(r'`([/!][^`]+)`', text) if section == 'Added' else []
            items.append({
                'title': match.group(1).rstrip(':. '),
                'section': section,
                'slash': next((c for c in commands if c.startswith('/')), None),
                'prefix': next((c for c in commands if c.startswith('!')), None),
            })
    return items[:max_items]
