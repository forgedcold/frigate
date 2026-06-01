# Frigate Dirty Checkout Archive - 2026-06-01

This branch archives the old dirty Frigate checkout that was replaced by the
clean `custom/v0.17.1-tiered-storage` source-of-truth branch.

Archived local checkout:

```text
/home/roger/frigate-archive-dirty-20260601
```

Original branch:

```text
feature/dual-stream-playback
```

Archive branch created in that checkout:

```text
archive/dirty-dual-stream-playback-2026-06-01
```

The full Git refs bundle was split into parts to stay below GitHub's large-file
limit. To reconstruct it:

```bash
cat archive/frigate-old-all-refs-20260601.bundle.parts/part-* > frigate-old-all-refs-20260601.bundle
git bundle verify frigate-old-all-refs-20260601.bundle
```

Companion files:

- `archive/frigate-old-dirty-tracked-20260601.patch`
- `archive/frigate-old-untracked-files-20260601.tgz`
- `archive/frigate-old-untracked-files-20260601.txt`
- `archive/frigate-old-status-20260601.txt`
- `archive/Dockerfile.overlay.validation-only`
