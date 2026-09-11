# Life Recorder

A native iPhone recorder and private local receiver. The iPhone records approximately one-minute AAC chunks. A Windows 11 or macOS receiver transcribes them locally with whisper.cpp and maintains one continuous Markdown transcript with hourly markers. Audio is deleted after durable receipt and successful transcription. No paid transcription service or cloud backend is required.

## Requirements

- Windows 11 for the receiver, with Python 3.10 or newer
- `ffmpeg.exe`, `whisper-cli.exe` from whisper.cpp, OpenSSL, and a downloaded GGML/GGUF Whisper model
- An iPhone running a supported iOS version; Developer Mode is needed for development or sideloaded builds
- A reachable HTTPS path between the phone and computer (same LAN by default; use a private VPN for cellular access)

The receiver uses only Python’s standard library. Put `ffmpeg.exe`, `whisper-cli.exe`, and `openssl.exe` on `PATH`, or pass their full paths to the Windows installer. Runtime data defaults to `%LOCALAPPDATA%\LifeRecorder`, outside this repository. Setup also applies a private Windows ACL to that directory.

## Configure the Windows receiver

From a PowerShell prompt in the repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\receiver\install_windows.ps1 `
  -Model 'C:\models\ggml-small.bin' `
  -Url 'https://192.168.1.42:8765' `
  -InstallTask
```

Use the Windows PC’s reachable LAN address in `-Url`; a hostname is also valid if the iPhone can resolve it. Omit `-Url` to use `https://<Windows-hostname>:8765`. If the tools are not on `PATH`, add switches such as:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\receiver\install_windows.ps1 `
  -Model 'C:\models\ggml-small.bin' `
  -Whisper 'C:\tools\whisper-cli.exe' `
  -Ffmpeg 'C:\tools\ffmpeg.exe' `
  -OpenSSL 'C:\tools\openssl.exe' `
  -InstallTask
```

Setup creates a random bearer token, a self-signed TLS certificate, the SHA-256 certificate fingerprint, a private pairing page, and `launch-arguments.json` in the data directory. Open `pairing.html` only on the intended iPhone and keep it private. The generated `start-receiver.ps1` contains no token and can also be started manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$env:LOCALAPPDATA\LifeRecorder\start-receiver.ps1"
```

`-InstallTask` creates or updates a least-privilege Windows Task Scheduler task named `Life Recorder Receiver`. It runs at sign-in and is configured to restart a failed receiver a few times. If a different task with that name already exists, setup refuses to modify it.

If Windows Defender Firewall blocks the LAN connection, add a narrow Private-profile rule for the chosen port; do not open the port on the Public profile:

```powershell
New-NetFirewallRule -DisplayName 'Life Recorder Receiver (Private)' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
  -RemoteAddress LocalSubnet -Profile Private
```

The receiver still requires both HTTPS certificate pinning and the bearer token. It exposes only authenticated health and upload endpoints; it does not expose transcript downloads or arbitrary computer access.

The combined transcript is always written to `life.md` in the data directory, with the same hourly markers and cleanup behavior as upstream. Completed audio is removed only after the receiver has durably recorded the receipt, transcribed the clip, exported `life.md`, and verified the phone’s acknowledgement. You can move `life.md` elsewhere and leave a symlink at the runtime path if desired.

Run the receiver’s Windows/Python protocol tests from the repository root:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
```

For an end-to-end audio check, pass a local model and any WAV/M4A fixture to `tests/audio_smoke.py` with `--model`, `--sample`, and `--report`; the script discovers or accepts explicit paths for all three external tools.

## iOS build without a Mac

The native iPhone source remains in `ios/LifeRecorder.xcodeproj`. The included [iOS unsigned build workflow](.github/workflows/ios-build.yml) uses a GitHub-hosted macOS/Xcode runner to compile the `LifeRecorder` app target in Debug for the iOS Simulator with code signing disabled. It needs no Apple credentials and produces no installable iPhone app.

The workflow is a compile check only. A simulator build cannot be installed on a physical iPhone.

### Signing and installing on an iPhone from Windows

- TestFlight is the cleanest Apple-supported route. A paid Apple Developer account is needed to create and upload a signed archive from a later macOS GitHub Actions workflow; then install the build through TestFlight on the iPhone. The signing material belongs in GitHub encrypted secrets or another secure signing service, never in this repository.
- A signed development or Ad Hoc IPA can be built on the GitHub macOS runner. Development/Ad Hoc distribution normally requires the iPhone to be registered and a matching provisioning profile. The resulting IPA still needs TestFlight or a compatible Windows sideloading tool; Apple’s Windows device utilities do not replace Xcode for arbitrary development IPA installation.
- Personal-team sideloading tools such as AltStore, SideStore, or Sideloadly can sign an IPA from Windows with the user’s own Apple account. Free signing is time-limited and requires periodic refresh; use only software and account prompts you trust. Do not send an Apple ID password to Codex, and do not commit Apple credentials, certificates, profiles, device identifiers, or tokens.

The current workflow intentionally stops before signing, provisioning, App Store Connect upload, or device installation.

## Recording behavior

Tap the recorder switch once. Recording continues while the screen is locked and while other apps are used. If the iPhone is rebooted or the app is force-quit, iOS requires opening Life Recorder once before microphone capture can resume. Pending audio remains on the phone until the receiver acknowledges it. Upload tasks are retried and stale connectivity tasks are cancelled so they cannot hold the queue indefinitely.

Whisper runs locally on the receiver. The receiver removes common stage-direction markers and highly repetitive hallucinated noise, then writes one continuous document with an hourly capture marker. This is cleanup, not a guarantee of perfect transcription.

## Security and limits

The default connection is local-LAN HTTPS with certificate pinning and a 256-bit random bearer token. The receiver has no public tunnel or cloud storage built in. A private VPN is required for cellular uploads outside the home network. Anyone who can read the private runtime directory can read the token and transcript, so keep that directory private and out of backups or repositories as appropriate.

## License

MIT. See [LICENSE](LICENSE).
