#!/usr/bin/env python3
"""Destructive-in-slot, self-restoring save/load test for USB-1.

The selected save-state slot is renamed out of the way and restored in a
finally block. The ordinary AM2R save is never opened for writing.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import posixpath
import re
import shlex
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import mister_ssh  # noqa: E402


STATE_ROOT = "/media/fat/savestates/AM2R"
WRAPPER_LOG = "/tmp/am2r-wrapper.log"
SAVE_PATH = "/media/fat/saves/AM2R/sav1"
SPACE_FLOOR = 256 * 1024 * 1024
LINUX_ENOTSUP = 95


def run(client, command: str, *, check: bool = True, timeout: int = 300) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdout.channel.settimeout(timeout)
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    status = stdout.channel.recv_exit_status()
    if check and status != 0:
        raise RuntimeError(
            f"USB-1 command failed ({status}): {command}\n{error.strip()}"
        )
    return output


def exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def log_tail(sftp, offset: int) -> tuple[int, str]:
    if not exists(sftp, WRAPPER_LOG):
        return 0, ""
    with sftp.open(WRAPPER_LOG, "rb") as stream:
        stream.seek(offset)
        data = stream.read()
        return stream.tell(), data.decode("utf-8", errors="replace")


def wait_for_log(sftp, offset: int, marker: str, timeout: float) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    accumulated = ""
    while time.monotonic() < deadline:
        offset, chunk = log_tail(sftp, offset)
        accumulated += chunk
        if marker in accumulated:
            return offset, accumulated
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for wrapper log marker: {marker}")


def line_has(log: str, marker: str, result: int) -> bool:
    expected = f"result={result}"
    return any(marker in line and expected in line for line in log.splitlines())


def hash_or_missing(client, path: str) -> str:
    quoted = shlex.quote(path)
    output = run(
        client,
        f"if test -f {quoted}; then sha256sum {quoted}; else printf 'missing\\n'; fi",
    )
    return output.strip().split()[0]


def free_bytes(client) -> int:
    lines = run(client, "df -Pk /media/fat").strip().splitlines()
    if len(lines) < 2:
        raise RuntimeError("cannot parse /media/fat free space")
    return int(lines[-1].split()[3]) * 1024


def assert_runtime_alive(client, timeout: float = 10.0) -> None:
    # The runner socket is deliberately closed while the process is quiesced,
    # including the fast ENOSPC refusal path. Give the resumed runner time to
    # publish its replacement socket before declaring the live core dead.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = run(
            client,
            "if test -S /tmp/am2r-state-wrapper.sock && "
            "test -S /tmp/am2r-state-runner.sock; then printf ready; fi",
            check=False,
        )
        if ready == "ready":
            return
        time.sleep(0.1)
    raise RuntimeError("AM2R save-state control sockets did not return")


def assert_restored_runtime_stable(
    client, restart_pid: int, duration: float = 5.0
) -> None:
    """Reject a restore that briefly reconnects and then crashes/relaunches."""
    assert_runtime_alive(client)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        ready = run(
            client,
            f"if kill -0 {restart_pid} 2>/dev/null && "
            "test -S /tmp/am2r-state-wrapper.sock && "
            "test -S /tmp/am2r-state-runner.sock; then printf ready; fi",
            check=False,
        )
        if ready != "ready":
            raise RuntimeError(
                "restored runner exited during stability window"
            )
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, choices=range(1, 5), default=4)
    parser.add_argument(
        "--helper",
        type=pathlib.Path,
        default=ROOT / "data" / "build" / "am2r_state_request",
    )
    parser.add_argument("--timeout", type=int, default=210)
    args = parser.parse_args()
    helper = args.helper.resolve()
    if not helper.is_file():
        raise SystemExit(f"ARM state-request helper is missing: {helper}")

    slot = args.slot
    token = f"{os.getpid()}-{int(time.time())}"
    image = posixpath.join(STATE_ROOT, f"slot{slot}.dmtcp")
    metadata = posixpath.join(STATE_ROOT, f"slot{slot}.txt")
    backup_image = f"{image}.hardware-test-backup-{token}"
    backup_metadata = f"{metadata}.hardware-test-backup-{token}"
    remote_helper = f"/tmp/am2r-state-request-{token}"
    moved_image = False
    moved_metadata = False
    slot_isolated = False
    client = mister_ssh.connect("USB-1")
    sftp = client.open_sftp()
    try:
        core = run(client, "tr -d '\\r\\n' < /tmp/CORENAME").strip()
        if core != "AM2R":
            raise RuntimeError(f"USB-1 must already be running AM2R, found {core!r}")
        assert_runtime_alive(client)
        if exists(sftp, f"{image}.new") or exists(sftp, f"{metadata}.new"):
            raise RuntimeError("a save-state publication is already in progress")

        ordinary_before = hash_or_missing(client, SAVE_PATH)
        oom_before = run(
            client,
            "dmesg | grep -Ec 'Out of memory|Killed process.*butterscotch' || true",
        ).strip()
        if exists(sftp, image):
            sftp.rename(image, backup_image)
            moved_image = True
        if exists(sftp, metadata):
            sftp.rename(metadata, backup_metadata)
            moved_metadata = True
        slot_isolated = True

        sftp.put(str(helper), remote_helper)
        run(client, f"chmod 700 {shlex.quote(remote_helper)}")
        log_offset = sftp.stat(WRAPPER_LOG).st_size if exists(sftp, WRAPPER_LOG) else 0
        available = free_bytes(client)
        run(client, f"{shlex.quote(remote_helper)} save {slot}")

        if available < SPACE_FLOOR:
            log_offset, save_log = wait_for_log(
                sftp,
                log_offset,
                f"savestate_capture_complete slot={slot}",
                15,
            )
            if not line_has(
                save_log, f"savestate_capture_complete slot={slot}", 28
            ):
                raise RuntimeError("low-space save did not return ENOSPC")
            if exists(sftp, image):
                raise RuntimeError("low-space save unexpectedly published a slot")
            assert_runtime_alive(client)
            outcome = "low-space refusal"
        else:
            log_offset, save_log = wait_for_log(
                sftp,
                log_offset,
                f"savestate_save_complete slot={slot}",
                args.timeout,
            )
            if not line_has(
                save_log, f"savestate_save_complete slot={slot}", 0
            ):
                raise RuntimeError("save-state publication failed")
            if not exists(sftp, image) or sftp.stat(image).st_size <= 4096:
                raise RuntimeError("save-state image was not published")
            if not exists(sftp, metadata):
                raise RuntimeError("save-state metadata was not published")
            saved_hash = hash_or_missing(client, image)
            if free_bytes(client) < SPACE_FLOOR:
                second_offset = log_offset
                run(client, f"{shlex.quote(remote_helper)} save {slot}")
                log_offset, low_log = wait_for_log(
                    sftp,
                    second_offset,
                    f"savestate_capture_complete slot={slot}",
                    15,
                )
                if not line_has(
                    low_log, f"savestate_capture_complete slot={slot}", 28
                ):
                    raise RuntimeError("second low-space save did not return ENOSPC")
                if hash_or_missing(client, image) != saved_hash:
                    raise RuntimeError("low-space refusal changed the valid slot")
                assert_runtime_alive(client)

            # Checkpoint completion is followed by a runner-side resume marker.
            # Drain that marker before requesting a load; otherwise it can be
            # mistaken for the later DMTCP restart's restore notification.
            log_offset, _ = wait_for_log(
                sftp,
                log_offset,
                f"savestate_restored slot={slot}",
                15,
            )

            load_offset = log_offset
            run(client, f"{shlex.quote(remote_helper)} load {slot}")
            log_offset, restart_log = wait_for_log(
                sftp,
                load_offset,
                f"savestate_restart slot={slot}",
                30,
            )
            restart_pids = re.findall(
                rf"savestate_restart slot={slot} pid=(\d+)", restart_log
            )
            if not restart_pids:
                raise RuntimeError("save-state restart PID was not logged")
            log_offset, load_log = wait_for_log(
                sftp,
                log_offset,
                f"savestate_restored slot={slot}",
                30,
            )
            if f"savestate_restored slot={slot}" not in load_log:
                raise RuntimeError("save-state load did not restore the runner")
            assert_restored_runtime_stable(client, int(restart_pids[-1]))

            # ARM DMTCP can restore a fresh-launch checkpoint, but a new image
            # captured from that restored process is not reliably restartable.
            # The frontend must reject this generation before touching the
            # existing slot, resume the game, and keep the loaded process live.
            nested_hash = hash_or_missing(client, image)
            run(client, f"{shlex.quote(remote_helper)} save {slot}")
            log_offset, nested_log = wait_for_log(
                sftp,
                log_offset,
                f"savestate_nested_rejected slot={slot}",
                15,
            )
            if f"errno={LINUX_ENOTSUP}" not in nested_log:
                raise RuntimeError("nested save was not rejected with ENOTSUP")
            if hash_or_missing(client, image) != nested_hash:
                raise RuntimeError("nested save rejection changed the valid slot")
            assert_restored_runtime_stable(client, int(restart_pids[-1]))
            outcome = "save/load plus safe nested-save refusal"

        ordinary_after = hash_or_missing(client, SAVE_PATH)
        if ordinary_after != ordinary_before:
            raise RuntimeError("ordinary AM2R save changed during state test")
        oom_after = run(
            client,
            "dmesg | grep -Ec 'Out of memory|Killed process.*butterscotch' || true",
        ).strip()
        if oom_after != oom_before:
            raise RuntimeError("kernel OOM signature appeared during state test")
        print(
            f"USB-1 save-state hardware regression passed: slot {slot} {outcome}; "
            "runner alive, no OOM, ordinary save unchanged"
        )
        return 0
    finally:
        try:
            if exists(sftp, remote_helper):
                sftp.remove(remote_helper)
            if exists(sftp, f"{image}.new"):
                sftp.remove(f"{image}.new")
            if exists(sftp, f"{metadata}.new"):
                sftp.remove(f"{metadata}.new")
            if slot_isolated and exists(sftp, image):
                sftp.remove(image)
            if slot_isolated and exists(sftp, metadata):
                sftp.remove(metadata)
            if moved_image and exists(sftp, backup_image):
                sftp.rename(backup_image, image)
            if moved_metadata and exists(sftp, backup_metadata):
                sftp.rename(backup_metadata, metadata)
        finally:
            sftp.close()
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
