#!/usr/bin/env python3
"""Restore into an empty directory; see backup.py restore --help."""
import sys
from backup import main
sys.argv.insert(1, 'restore')
if __name__ == '__main__':
    main()
