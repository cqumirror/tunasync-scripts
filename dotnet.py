#!/usr/bin/env python3
"""
.NET Release Mirror Script
Syncs .NET SDK, Runtime, ASP.NET Core, and Windows Desktop binaries from official metadata.
"""

import argparse
import concurrent.futures
import json
import logging
import os
import re
import subprocess
import tempfile
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d - %(filename)s:%(lineno)d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
handler.setFormatter(formatter)
logger.addHandler(handler)
BASE_URL = os.getenv("TUNASYNC_UPSTREAM_URL", "https://builds.dotnet.microsoft.com/dotnet")
DEFAULT_INDEX_URL = BASE_URL+"/release-metadata/releases-index.json"
UA = "cqu-dotnet-release-downloader/1.0 (+https://mirrors.cqu.edu.cn)"
TIMEOUT = (30, 60)


class HashMismatchError(Exception):
    pass


def _verify_file_hash(file_path: Path, expected_hash: str) -> bool:
    """Verify file hash (SHA512)"""
    sha512 = hashlib.sha512()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha512.update(chunk)
    actual_hash = sha512.hexdigest()
    return actual_hash.lower() == expected_hash.lower()


class DotNetMirror:
    def __init__(self, config: Dict, working_dir: Path, workers: int = 1, fast_skip: bool = False):
        self.config = config
        self.working_dir = working_dir
        self.workers = workers
        self.fast_skip = fast_skip
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        self.futures: List[concurrent.futures.Future] = []
        self.remote_filelist: Set[Path] = set()
        self.headers = {"User-Agent": UA}

    def run(self) -> bool:
        """Main entry point"""
        logger.info(f"Fetching release index from {DEFAULT_INDEX_URL}")
        try:
            resp = requests.get(DEFAULT_INDEX_URL, timeout=TIMEOUT)
            resp.raise_for_status()
            index = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch release index: {e}")
            return False

        channels = index.get("releases-index", [])

        # Filter channels based on config
        filtered_channels = self._filter_channels(channels)
        logger.info(f"Selected channels: {[ch['channel-version'] for ch in filtered_channels]}")

        # Process each channel
        for channel in filtered_channels:
            self._process_channel(channel)

        # Wait for all downloads
        results, _ = concurrent.futures.wait(self.futures)
        self.executor.shutdown()
        success = True
        hash_mismatch = False
        for future in results:
            try:
                success = future.result() and success
            except HashMismatchError:
                hash_mismatch = True
                success = False
            except Exception:
                success = False

        # Clean up old files
        if self.config.get("cleanup", True):
            self._cleanup_old_files()

        if hash_mismatch:
            return 24
        return 0 if success else 1

    def _filter_channels(self, channels: List[Dict]) -> List[Dict]:
        """Filter channels based on config"""
        include_versions = self.config.get("include_versions", [])
        exclude_versions = self.config.get("exclude_versions", [])
        include_eol = self.config.get("include_eol", False)
        include_pre_release = self.config.get("include_pre_release", False)

        filtered = []
        for ch in channels:
            ver = ch.get("channel-version", "")
            support_phase = ch.get("support-phase", "")

            # Version blacklist/whitelist
            if include_versions and ver not in include_versions:
                continue
            if ver in exclude_versions:
                continue

            # EOL filter
            if not include_eol and support_phase == "eol":
                continue

            # Pre-release filter
            if not include_pre_release and support_phase == "preview":
                continue

            filtered.append(ch)
        return filtered

    def _process_channel(self, channel: Dict):
        """Process a single .NET channel/version"""
        channel_ver = channel["channel-version"]
        releases_json_url = channel["releases.json"]

        try:
            resp = requests.get(releases_json_url, timeout=TIMEOUT)
            resp.raise_for_status()
            releases_data = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch releases for {channel_ver}: {e}")
            return

        all_releases = releases_data.get("releases", [])
        if not all_releases:
            logger.warning(f"No releases found for {channel_ver}")
            return

        # Sort releases by version (newest first)
        all_releases.sort(
            key=lambda x: self._version_to_tuple(x.get("release-version", "")),
            reverse=True
        )

        # Limit releases per version
        max_releases = self.config.get("max_releases_per_version")
        if max_releases is not None and max_releases > 0:
            all_releases = all_releases[:max_releases]

        logger.info(f"Selected {len(all_releases)} releases for {channel_ver}")

        # Create LatestRelease link
        if all_releases:
            latest_version = all_releases[0].get("release-version", "")
            if latest_version:
                self._link_latest_version(channel_ver, latest_version)

        for release in all_releases:
            self._process_release(channel_ver, release)

    def _process_release(self, channel_ver: str, release: Dict):
        """Process a single release, downloading configured components"""
        release_version = release.get("release-version", "")
        if not release_version:
            logger.warning("Release without version, skipping")
            return

        release_dir = self.working_dir / channel_ver / release_version
        release_dir.mkdir(parents=True, exist_ok=True)

        # Get component patterns from config
        include_patterns = self.config.get("include_components", [])
        exclude_patterns = self.config.get("exclude_components", [])

        # Iterate over all top-level keys in release that have file data
        for component_key, component_data in release.items():
            if not isinstance(component_data, dict) or "files" not in component_data:
                continue

            # Check if component matches include/exclude patterns
            if not self._should_include_component(component_key, include_patterns, exclude_patterns):
                continue

            logger.debug(f"Processing component {component_key} for {release_version}")
            self._download_component_files(component_data, release_dir, release_version)

    def _should_include_component(self, component: str, include_patterns: List[str],
                                  exclude_patterns: List[str]) -> bool:
        """Check if component should be included based on regex patterns"""
        # If include patterns exist, component must match at least one
        if include_patterns:
            if not any(re.search(f"^{p}$", component, re.IGNORECASE) for p in include_patterns):
                return False

        # Component must not match any exclude pattern
        if exclude_patterns:
            if any(re.search(p, component, re.IGNORECASE) for p in exclude_patterns):
                return False

        return True

    def _download_component_files(self, component_data: Dict, release_dir: Path, version: str):
        """Download all files from a component"""
        files = component_data.get("files", [])
        exclude_patterns = self.config.get("exclude_files", [])

        for file_info in files:
            url = file_info.get("url")
            if not url:
                continue

            filename = file_info.get("name", "")
            if not filename:
                continue

            # Check filename exclusion
            if self._should_exclude_file(filename, exclude_patterns):
                logger.debug(f"Excluding file {filename} by pattern")
                continue

            dst_file = release_dir / filename
            self.remote_filelist.add(dst_file.relative_to(self.working_dir))

            # Check if file exists and skip/verify
            if dst_file.is_file():
                if self.fast_skip:
                    logger.debug(f"Fast skipping {filename}")
                    continue

                remote_hash = file_info.get("hash", "")
                if remote_hash:
                    if _verify_file_hash(dst_file, remote_hash):
                        logger.debug(f"Skipping {filename} (hash matches)")
                        continue
                else:
                    local_size = dst_file.stat().st_size
                    remote_size = file_info.get("size", -1)
                    if remote_size != -1 and local_size == remote_size:
                        logger.debug(f"Skipping {filename} (size matches)")
                        continue

            dst_file.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Queueing {filename}")
            self.futures.append(
                self.executor.submit(
                    self._download_file, url, dst_file, file_info.get("size", -1), file_info.get("hash", "")
                )
            )

    def _should_exclude_file(self, filename: str, patterns: List[str]) -> bool:
        """Check if file should be excluded based on regex patterns"""
        if not patterns:
            return False
        return any(re.search(p, filename, re.IGNORECASE) for p in patterns)

    def _download_file(self, url: str, dst_file: Path, expected_size: int, expected_hash: str = "") -> bool:
        """Download a single file with atomic temp file and size/hash verification"""
        logger.info(f"Downloading {dst_file.name} from {url}")
        try:
            with requests.get(url, stream=True, timeout=TIMEOUT) as r:
                r.raise_for_status()
                tmp_file = None
                try:
                    with tempfile.NamedTemporaryFile(
                            prefix=f".{dst_file.name}.",
                            suffix=".tmp",
                            dir=dst_file.parent,
                            delete=False,
                    ) as f:
                        tmp_file = Path(f.name)
                        for chunk in r.iter_content(chunk_size=1 << 20):
                            if chunk:
                                f.write(chunk)

                    # Verify size
                    if expected_size != -1 and tmp_file.stat().st_size != expected_size:
                        raise Exception(f"Size mismatch: expected {expected_size}, got {tmp_file.stat().st_size}")

                    # Verify hash (SHA512)
                    if expected_hash:
                        if not _verify_file_hash(tmp_file, expected_hash):
                            raise HashMismatchError(f"Hash mismatch for {dst_file.name}")

                    tmp_file.chmod(0o644)
                    tmp_file.replace(dst_file)
                    logger.info(f"Downloaded {dst_file.name}")
                    return True
                finally:
                    if tmp_file and tmp_file.is_file():
                        tmp_file.unlink()
        except HashMismatchError as e:
            logger.error(f"Failed to download {url}: {e}")
            if dst_file.is_file():
                dst_file.unlink()
            raise
        except Exception as e:
            logger.error(f"Failed to download {url}: {e}")
            if dst_file.is_file():
                dst_file.unlink()
            return False

    def _link_latest_version(self, channel_ver: str, latest_version: str):
        """Create symbolic link 'LatestRelease' pointing to the latest version directory"""
        channel_dir = self.working_dir / channel_ver
        latest_link = channel_dir / "LatestRelease"

        target_dir = channel_dir / latest_version
        target = Path(latest_version)

        try:
            if not target_dir.is_dir():
                logger.warning(f"Latest version directory {target_dir} does not exist yet, will retry later")
                return
            if latest_link.is_symlink() or latest_link.exists():
                latest_link.unlink()
            latest_link.symlink_to(target, target_is_directory=True)

        except OSError as e:
            logger.warning(f"Failed to create symlink: {e}")

    def _cleanup_old_files(self):
        """Remove files that are no longer in the remote filelist"""
        logger.info("Cleaning up old files...")

        # Collect all local files
        local_files = set()
        for f in self.working_dir.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(self.working_dir)
                if not f.is_symlink():
                    local_files.add(rel_path)

        # Delete files not in remote list
        to_delete = local_files - self.remote_filelist
        for f in to_delete:
            full_path = self.working_dir / f
            logger.info(f"Deleting {f}")
            full_path.unlink()

        # Remove empty version directories (matching pattern X.Y.Z)
        for d in sorted(self.working_dir.rglob("*"), reverse=True):
            if d.is_dir() and self._is_version_dir(d.name):
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                        logger.info(f"Removed empty directory {d.relative_to(self.working_dir)}")
                except OSError:
                    pass

    def _is_version_dir(self, dirname: str) -> bool:
        """Check if directory name matches version pattern (e.g., 3.0.0, 10.0.8-preview.4)"""
        return bool(re.match(r"^\d+\.\d+", dirname))

    @staticmethod
    def _version_to_tuple(version: str) -> Tuple:
        """Convert version string to sortable tuple"""
        parts = re.split(r"[.-]", version)
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(p)
        return tuple(result)


def load_config(config_path: Path) -> Dict:
    """Load and validate configuration"""
    with open(config_path, "r") as f:
        config = json.load(f)

    # Set defaults
    config.setdefault("include_components", [])
    config.setdefault("exclude_components", [])
    config.setdefault("include_versions", [])
    config.setdefault("exclude_versions", [])
    config.setdefault("exclude_files", [])
    config.setdefault("cleanup", True)
    config.setdefault("include_eol", False)
    config.setdefault("include_pre_release", False)
    config.setdefault("max_releases_per_version", None)

    return config


def main():
    parser = argparse.ArgumentParser(description="Sync .NET releases from official metadata")
    parser.add_argument("--config", required=True, help="JSON configuration file")
    parser.add_argument("--working-dir", default=os.getenv("TUNASYNC_WORKING_DIR"),
                        help="Working directory for downloads")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent downloads")
    parser.add_argument("--fast-skip", action="store_true", help="Skip size verification for existing files")
    args = parser.parse_args()

    if args.working_dir is None:
        raise Exception("Working directory is required (--working-dir or TUNASYNC_WORKING_DIR)")

    working_dir = Path(args.working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(Path(args.config))
    logger.info(f"Configuration loaded from {args.config}")

    mirror = DotNetMirror(
        config=config,
        working_dir=working_dir,
        workers=args.workers,
        fast_skip=args.fast_skip,
    )

    exit_code = mirror.run()

    total_size = subprocess.check_output(["du", "-sh", str(working_dir)], text=True).split()[0]
    logger.info(f"Total size is {total_size}")

    if exit_code != 0:
        logger.error("Sync completed with errors")
        exit(exit_code)

    logger.info("Sync completed successfully")


if __name__ == "__main__":
    main()