# Forum Scraper v1.0 Baseline

**Backup Date**: 2026-01-17
**Description**: 
This is the stable baseline of the forum scraper project before the implementation of Phase 4 (Stealth Mode) and Phase 5 (Image Localization).

## Included Features
1.  **Manual Login**: `01_login.py` (Functional)
2.  **Essence Link Crawler**: `02_extract_essence_links.py` (Functional, resilient)
3.  **Recursive Extractor**: `03_extract_content.py` (Basic functionality, prone to bans/empty content)
4.  **Cleanup Utilities**: `cleanup_failed.py` (Includes logic for hidden content & skeleton detection)

## State Snapshot
- **Output**: Contains ~30 Verified High-Quality Markdown files.
- **Progress**: `progress.json` has been reset. All failed tasks are safely queued.
- **Queue**: ~4100 items pending.

## Next Steps (in v2)
- Playwright Stealth integration.
- Image/Media localization.
- Human-like behavioral simulation.

> [!NOTE]
> Use this folder to rollback if v2 development introduces unstable regressions.
