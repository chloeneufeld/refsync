import os
import tempfile

# Point refsync at a throwaway data dir *before* refsync.config is imported,
# so tests never touch ~/.refsync.
os.environ.setdefault("REFSYNC_DATA_DIR", tempfile.mkdtemp(prefix="refsync-test-"))
