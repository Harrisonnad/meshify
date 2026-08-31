# WSL2 + CUDA Setup

Status as of 2026-08-30: **Done, all 4 steps.** BIOS SVM enabled, Ubuntu installed with user
`blitz`, GPU passthrough verified (`nvidia-smi` inside WSL sees the RTX 2070, driver 615.65.06,
CUDA UMD 13.4), and CUDA Toolkit 12.6 installed via the `wsl-ubuntu` apt repo (`nvcc` confirms
`release 12.6, V12.6.85`). Chose 12.6 over the newer 12.8/12.9/13.x meta-packages available in
the repo because current PyTorch wheels and the Phase 1 model repos (InstantMesh/TripoSR,
ComfyUI) target CUDA 12.x — staying a step behind the bleeding edge avoids nvcc/wheel
mismatches when building CUDA extensions from source. Turing (sm_75) is well within 12.6's
supported range.

Next: **Phase 1** — ComfyUI chain validation on Turing (see [DECISIONS.md](./DECISIONS.md)).

## Why WSL2 at all

Master plan §2: roughly half the flash-attention and CUDA-extension build steps in these
repos assume Linux, and native Windows will cost days. Given this machine's Turing GPU
already puts Phase 1 in the high-risk column (see [hardware.md](./hardware.md)), we don't
also want to be fighting the toolchain.

## Step 1 — Windows features (DONE)

Enabled 2026-08-22, elevated:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
```

Both report `State: Enabled`, `RestartNeeded: True`.

## Step 2 — Enable SVM Mode in BIOS (BLOCKED — requires physical access)

`systeminfo` reports **`Virtualization Enabled In Firmware: No`**. WSL2 cannot run without
hardware virtualization; it fails with `0x80370102` regardless of the Windows features above.

The Ryzen 7 5800X supports AMD-V — it is simply switched off in firmware.

**Board:** ASUS ROG STRIX B450-F GAMING, AMI BIOS 5801 (2025-09-09)

1. Reboot and press **`Delete`** (or `F2`) at the ASUS splash to enter UEFI.
2. Press **`F7`** for Advanced Mode.
3. Go to **Advanced → CPU Configuration**.
4. Set **SVM Mode** to **Enabled**. (AMD's name for virtualization; Intel calls it VT-x.)
5. **`F10`** to save and exit.

Since a reboot is already pending from Step 1, doing the BIOS change on that same reboot
costs one restart instead of two.

## Step 3 — Verify, then install the distro

After the reboot:

```powershell
systeminfo | Select-String "Virtualization Enabled In Firmware"   # expect: Yes
wsl --install -d Ubuntu
wsl --set-default-version 2
wsl --status
```

## Step 4 — CUDA inside the distro

**Do not install a Linux NVIDIA driver.** The Windows driver (610.88) passes the GPU through
to WSL2; installing a second driver inside the distro breaks it. Install only the CUDA
toolkit, using NVIDIA's WSL-Ubuntu package (which deliberately excludes the driver):

```bash
nvidia-smi   # should work immediately inside WSL, showing the RTX 2070
```

Then follow NVIDIA's current `cuda-toolkit` instructions for the `wsl-ubuntu` repo target.
Verify the toolkit version matches what the model repos expect before building anything.

## Notes for later

- The 8 GB of VRAM is **shared with the Windows desktop**, which holds ~1.4 GB at idle.
  Close browsers and Godot/Unreal before a generation run.
- WSL2 memory defaults to a large share of the 32 GB host RAM. If that becomes a problem,
  cap it in `%UserProfile%\.wslconfig`.
- Keep model weights on the Linux filesystem (`~/`), not `/mnt/c/`. Cross-OS file access in
  WSL2 is dramatically slower and these are multi-GB reads.
