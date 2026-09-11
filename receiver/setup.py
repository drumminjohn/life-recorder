#!/usr/bin/env python3
"""Prepare private receiver credentials and a certificate-pinned iPhone pairing link."""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse

from receiver import Inbox, atomic_write, resolve_executable


TASK_NAME = "Life Recorder Receiver"
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def current_account() -> str:
    return subprocess.check_output(["whoami"], text=True).strip()


def current_sid() -> str:
    output = subprocess.check_output(["whoami", "/user"], text=True)
    match = re.search(r"S-\d-\d+(?:-\d+)+", output)
    if not match:
        raise RuntimeError("Could not determine the Windows account SID")
    return match.group(0)


def secure_windows_directory(root: Path):
    """Make the runtime directory private to the account that runs the receiver."""
    if os.name != "nt":
        return
    icacls = resolve_executable(None, "icacls.exe", "icacls")
    if not icacls:
        raise RuntimeError("Windows icacls.exe is required to protect the private runtime directory")
    try:
        account = current_account()
        paths = [root] + [path for path in root.rglob("*") if not path.is_symlink()]
        for path in paths:
            grant = f"{account}:(OI)(CI)F" if path.is_dir() else f"{account}:F"
            subprocess.run([icacls, str(path), "/inheritance:r", "/grant:r", grant, "/C"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Could not protect the private runtime directory") from error


def make_powershell_start_script(config: Path, root: Path, log: Path) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"Set-Location -LiteralPath {powershell_literal(str(root))}",
        f"$command = Get-Content -Raw -LiteralPath {powershell_literal(str(config))} | ConvertFrom-Json",
        "if ($command.Count -lt 2) { throw 'Invalid receiver configuration' }",
        f"& $command[0] $command[1..($command.Count - 1)] 2>&1 | Out-File -LiteralPath {powershell_literal(str(log))} -Append -Encoding utf8",
        "$exitCode = $LASTEXITCODE",
        "exit $exitCode",
        "",
    ])


def task_xml(script: Path, root: Path) -> bytes:
    powershell = resolve_executable(None, "powershell.exe", "powershell", "pwsh.exe", "pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is required to install the Windows startup task")
    namespace = f"{{{TASK_NAMESPACE}}}"
    ET.register_namespace("", TASK_NAMESPACE)

    task = ET.Element(namespace + "Task", {"version": "1.4"})
    registration = ET.SubElement(task, namespace + "RegistrationInfo")
    ET.SubElement(registration, namespace + "Description").text = (
        "Starts the private Life Recorder receiver for the signed-in Windows user."
    )
    triggers = ET.SubElement(task, namespace + "Triggers")
    logon = ET.SubElement(triggers, namespace + "LogonTrigger")
    ET.SubElement(logon, namespace + "Enabled").text = "true"
    principals = ET.SubElement(task, namespace + "Principals")
    principal = ET.SubElement(principals, namespace + "Principal", {"id": "Author"})
    ET.SubElement(principal, namespace + "UserId").text = current_sid()
    ET.SubElement(principal, namespace + "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, namespace + "RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(task, namespace + "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
    ):
        ET.SubElement(settings, namespace + name).text = value
    idle = ET.SubElement(settings, namespace + "IdleSettings")
    ET.SubElement(idle, namespace + "StopOnIdleEnd").text = "false"
    ET.SubElement(idle, namespace + "RestartOnIdle").text = "false"
    for name, value in (
        ("AllowStartOnDemand", "true"),
        ("Enabled", "true"),
        ("Hidden", "true"),
        ("RunOnlyIfIdle", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT0S"),
        ("Priority", "7"),
    ):
        ET.SubElement(settings, namespace + name).text = value
    restart = ET.SubElement(settings, namespace + "RestartOnFailure")
    ET.SubElement(restart, namespace + "Interval").text = "PT1M"
    ET.SubElement(restart, namespace + "Count").text = "3"
    actions = ET.SubElement(task, namespace + "Actions", {"Context": "Author"})
    action = ET.SubElement(actions, namespace + "Exec")
    ET.SubElement(action, namespace + "Command").text = powershell
    ET.SubElement(action, namespace + "Arguments").text = (
        f"-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File \"{script}\""
    )
    ET.SubElement(action, namespace + "WorkingDirectory").text = str(root)
    # schtasks.exe expects an XML declaration that matches a UTF-16 file.
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def task_targets_script(xml: str, script: Path) -> bool:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return False
    needle = str(script).replace("/", "\\").casefold()
    return any(needle in (element.text or "").replace("/", "\\").casefold()
               for element in root.iter())


def install_windows_task(root: Path, script: Path):
    schtasks = resolve_executable(None, "schtasks.exe", "schtasks")
    if not schtasks:
        raise RuntimeError("Windows schtasks.exe is required to install the startup task")
    query = subprocess.run([schtasks, "/Query", "/TN", TASK_NAME, "/XML"],
                           capture_output=True, text=True)
    if query.returncode == 0 and not task_targets_script(query.stdout, script):
        raise RuntimeError(f"A different Task Scheduler task named {TASK_NAME!r} already exists; it was not modified")
    if query.returncode not in (0, 1):
        raise RuntimeError("Could not inspect the existing Life Recorder Task Scheduler task")

    task_file = root / ".life-recorder-task.xml"
    atomic_write(task_file, task_xml(script, root))
    try:
        subprocess.run([schtasks, "/Create", "/TN", TASK_NAME, "/XML", str(task_file), "/F"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Could not create the Life Recorder Task Scheduler task") from error
    finally:
        task_file.unlink(missing_ok=True)
    try:
        subprocess.run([schtasks, "/Run", "/TN", TASK_NAME],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("The Life Recorder Task Scheduler task was created but could not be started") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="Private runtime directory outside the source repository")
    parser.add_argument("--model", type=Path, required=True,
                        help="Existing GGML/GGUF Whisper model")
    parser.add_argument("--url", help="Reachable HTTPS URL; defaults to this computer's hostname")
    parser.add_argument("--whisper", help="Path or PATH name of whisper-cli/whisper-cli.exe")
    parser.add_argument("--ffmpeg", help="Path or PATH name of ffmpeg/ffmpeg.exe")
    parser.add_argument("--openssl", help="Path or PATH name of openssl/openssl.exe")
    parser.add_argument("--install-agent", action="store_true",
                        help="Start the receiver at sign-in using Windows Task Scheduler")
    args = parser.parse_args()
    os.umask(0o077)
    root = args.data_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        secure_windows_directory(root)
    except RuntimeError as error:
        parser.error(str(error))

    model = args.model.expanduser().resolve()
    if not model.is_file():
        parser.error("--model must point to an existing Whisper model")
    whisper = resolve_executable(args.whisper, "whisper-cli.exe", "whisper-cli")
    ffmpeg = resolve_executable(args.ffmpeg, "ffmpeg.exe", "ffmpeg")
    if not whisper or not ffmpeg:
        parser.error("Could not find whisper-cli and ffmpeg; add them to PATH or pass --whisper and --ffmpeg")

    inbox = Inbox(root)
    url = args.url or f"https://{socket.gethostname()}:8765"
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        parser.error("--url contains an invalid port")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ("", "/") or
            (port is not None and not 1 <= port <= 65535)):
        parser.error("--url must be an HTTPS address without credentials, a query, or a path")

    cert, key = root / "receiver.crt", root / "receiver.key"
    if not cert.exists() or not key.exists():
        openssl = resolve_executable(args.openssl, "openssl.exe", "openssl")
        if not openssl:
            parser.error("OpenSSL is required to create the TLS certificate; add openssl.exe to PATH or pass --openssl")
        try:
            subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256", "-days", "365",
                            "-subj", "/CN=Life Recorder", "-keyout", str(key), "-out", str(cert)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as error:
            raise SystemExit("Could not create the TLS certificate with OpenSSL") from error
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))).hexdigest()
    pair = "liferecorder://pair?" + urlencode({"url": url, "token": inbox.token, "pin": pin})
    page = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair Life Recorder</title><style>
body{{font:18px system-ui;max-width:640px;margin:60px auto;padding:24px;line-height:1.55;background:#faf9f6;color:#202028}}
a{{display:inline-block;padding:14px 24px;background:#5145cd;color:white;border-radius:12px;text-decoration:none}}
code{{overflow-wrap:anywhere;font-size:14px}}dt{{margin-top:20px;color:#666}}dd{{margin:4px 0}}</style>
<h1>Pair your iPhone</h1><p>Install Life Recorder, then open this private page on your iPhone and tap below.</p>
<a href="{html.escape(pair, quote=True)}">Pair Life Recorder</a>
<p>On the same Wi-Fi, this computer must be awake. For cellular uploads, use a private VPN address and regenerate this page with <code>--url</code>.</p>
<details><summary>Enter settings manually</summary><dl>
<dt>Receiver</dt><dd><code>{html.escape(url)}</code></dd>
<dt>Pairing token</dt><dd><code>{html.escape(inbox.token)}</code></dd>
<dt>Certificate fingerprint</dt><dd><code>{pin}</code></dd></dl></details>
<p>This page contains your private pairing credential. Keep it private.</p>"""
    atomic_write(root / "pairing.html", page.encode("utf-8"))
    command = [sys.executable, str(Path(__file__).with_name("receiver.py").resolve()),
               "--data-dir", str(root), "--host", "0.0.0.0", "--port", str(port or 443),
               "--cert", str(cert), "--key", str(key), "--model", str(model),
               "--whisper", whisper, "--ffmpeg", ffmpeg]
    atomic_write(root / "launch-arguments.json", json.dumps(command).encode("utf-8"))
    if os.name == "nt":
        start = root / "start-receiver.ps1"
        atomic_write(start, make_powershell_start_script(root / "launch-arguments.json", root,
                                                         root / "receiver.log").encode("utf-8-sig"))
    else:
        start = root / "start-receiver.sh"
        atomic_write(start, ("#!/bin/sh\nexec " + shlex.join(command) + "\n").encode("utf-8"))
        os.chmod(start, 0o700)

    try:
        secure_windows_directory(root)
        if args.install_agent:
            if os.name != "nt":
                parser.error("--install-agent is only supported on Windows")
            install_windows_task(root, start)
    except RuntimeError as error:
        parser.error(str(error))

    if args.install_agent:
        print("Windows Task Scheduler configured to start the receiver at sign-in.")
    print(f"Receiver prepared: {url}")
    print(f"Private pairing page: {root / 'pairing.html'}")
    print(f"Start script: {start}")
    print("No credentials have been printed. Nothing has been made publicly reachable.")


if __name__ == "__main__":
    main()
