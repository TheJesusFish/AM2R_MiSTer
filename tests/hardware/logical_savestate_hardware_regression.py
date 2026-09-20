#!/usr/bin/env python3
"""Self-restoring logical save/load regression for the USB-1 MiSTer.

The selected .fast slot is moved aside and restored in a finally block. The
test never writes AM2R's ordinary saves or the legacy .dmtcp files.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import posixpath
import shlex
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import mister_ssh  # noqa: E402


STATE_ROOT = "/media/fat/savestates/AM2R"
WRAPPER_LOG = "/tmp/am2r-wrapper.log"
ORDINARY_FILES = (
    "/media/fat/saves/AM2R/config.ini",
    "/media/fat/saves/AM2R/sav1",
)


def run(client, command: str, *, check: bool = True, timeout: int = 30) -> str:
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


def log_size(sftp) -> int:
    return sftp.stat(WRAPPER_LOG).st_size if exists(sftp, WRAPPER_LOG) else 0


def wait_for_log(sftp, offset: int, marker: str, timeout: float) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    accumulated = ""
    while time.monotonic() < deadline:
        if exists(sftp, WRAPPER_LOG):
            with sftp.open(WRAPPER_LOG, "rb") as stream:
                stream.seek(offset)
                accumulated += stream.read().decode("utf-8", errors="replace")
                offset = stream.tell()
        if marker in accumulated:
            return offset, accumulated
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for wrapper marker: {marker}")


def runner_pid(client) -> int:
    output = run(client, "pidof butterscotch").strip().split()
    if len(output) != 1:
        raise RuntimeError(f"expected one runner PID, found {output!r}")
    return int(output[0])


def ordinary_hashes(client) -> str:
    commands = []
    for path in ORDINARY_FILES:
        quoted = shlex.quote(path)
        commands.append(
            f"if test -f {quoted}; then sha256sum {quoted}; "
            f"else printf 'missing  %s\\n' {quoted}; fi"
        )
    return run(client, "; ".join(commands)).strip()


def request_and_wait(
    client,
    sftp,
    helper: str,
    action: str,
    slot: int,
    offset: int,
    timeout: float,
) -> tuple[int, float, str]:
    marker = f"savestate_fast_{'saved' if action == 'save' else 'loaded'} slot={slot}"
    started = time.monotonic()
    run(client, f"{shlex.quote(helper)} {action} {slot}")
    offset, output = wait_for_log(sftp, offset, marker, timeout)
    return offset, (time.monotonic() - started) * 1000.0, output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, choices=range(1, 5), default=4)
    parser.add_argument(
        "--helper",
        type=pathlib.Path,
        default=ROOT / "data" / "build" / "am2r_logical_state_request",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    helper = args.helper.resolve()
    if not helper.is_file():
        raise SystemExit(
            f"ARM logical-state helper is missing: {helper}\n"
            "Build tools/am2r_logical_state_request.c with the MiSTer ARM compiler."
        )

    slot = args.slot
    token = f"{os.getpid()}-{int(time.time())}"
    state = posixpath.join(STATE_ROOT, f"slot{slot}.fast")
    temporary = f"{state}.new"
    backup = f"{state}.hardware-test-backup-{token}"
    valid_copy = f"{state}.hardware-test-valid-{token}"
    remote_helper = f"/tmp/am2r-logical-state-request-{token}"
    moved_state = False

    client = mister_ssh.connect("USB-1")
    sftp = client.open_sftp()
    try:
        core = run(client, "tr -d '\\r\\n' < /tmp/CORENAME").strip()
        if core != "AM2R":
            raise RuntimeError(f"USB-1 must already be running AM2R, found {core!r}")
        if not exists(sftp, "/tmp/am2r-state-runner.sock"):
            raise RuntimeError("logical save-state runner socket is unavailable")
        if exists(sftp, temporary):
            raise RuntimeError("a logical save publication is already in progress")
        if exists(sftp, backup) or exists(sftp, valid_copy):
            raise RuntimeError("hardware-test scratch path already exists")

        ordinary_before = ordinary_hashes(client)
        initial_pid = runner_pid(client)
        if exists(sftp, state):
            sftp.rename(state, backup)
            moved_state = True

        sftp.put(str(helper), remote_helper)
        run(client, f"chmod 700 {shlex.quote(remote_helper)}")
        offset = log_size(sftp)

        timings: list[tuple[str, float]] = []
        for generation in (1, 2):
            offset, elapsed, _ = request_and_wait(
                client, sftp, remote_helper, "save", slot, offset, args.timeout
            )
            timings.append((f"save{generation}", elapsed))
            if not exists(sftp, state) or sftp.stat(state).st_size <= 64:
                raise RuntimeError("logical state was not atomically published")
            if exists(sftp, temporary):
                raise RuntimeError("logical state temporary file remained after save")

            offset, elapsed, _ = request_and_wait(
                client, sftp, remote_helper, "load", slot, offset, args.timeout
            )
            timings.append((f"load{generation}", elapsed))
            if runner_pid(client) != initial_pid:
                raise RuntimeError("logical load replaced or crashed the runner process")

        sftp.rename(state, valid_copy)
        run(client, f"cp {shlex.quote(valid_copy)} {shlex.quote(state)}")
        run(client, f"printf X >> {shlex.quote(state)}")
        error_offset = offset
        run(client, f"{shlex.quote(remote_helper)} load {slot}")
        offset, error_log = wait_for_log(
            sftp, error_offset, f"savestate_error slot={slot}", args.timeout
        )
        if "errno=74" not in error_log:
            raise RuntimeError("corrupt logical state was not rejected with EBADMSG")
        if runner_pid(client) != initial_pid:
            raise RuntimeError("corrupt-state rejection crashed the runner")
        sftp.remove(state)
        sftp.rename(valid_copy, state)

        ordinary_after = ordinary_hashes(client)
        if ordinary_after != ordinary_before:
            raise RuntimeError("logical-state test changed an ordinary AM2R save")

        print("logical save-state hardware regression passed")
        print(f"slot={slot} bytes={sftp.stat(state).st_size} pid={initial_pid}")
        for label, elapsed in timings:
            print(f"{label}_ms={elapsed:.1f}")
        return 0
    finally:
        for path in (remote_helper, temporary):
            try:
                if exists(sftp, path):
                    sftp.remove(path)
            except OSError:
                pass
        try:
            if exists(sftp, valid_copy):
                if exists(sftp, state):
                    sftp.remove(state)
                sftp.rename(valid_copy, state)
        except OSError:
            pass
        try:
            if moved_state:
                if exists(sftp, state):
                    sftp.remove(state)
                if exists(sftp, backup):
                    sftp.rename(backup, state)
            elif exists(sftp, state):
                sftp.remove(state)
        finally:
            sftp.close()
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
