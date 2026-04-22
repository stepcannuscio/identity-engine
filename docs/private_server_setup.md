# Private Server Setup

This guide covers running identity-engine inference on a remote Ollama instance you control, instead of relying on local hardware or cloud APIs.

## Why use a private server?

- Your Mac doesn't have enough RAM/GPU for a capable local model (Intel Mac 16GB = slow 3B model)
- You want full-size model inference without sending data to Anthropic/Groq
- Your private server is trusted hardware you own — `local_only` attributes are allowed to travel there over an encrypted Tailscale tunnel

## Quick start

1. Set up an Ollama server (see options below)
2. In the app: **Settings → Providers → Private server → enter URL → Save**
3. Select the **Private server** profile
4. The backend pill in the header will show `private server`

Or via CLI:
```
make add-private-server-url URL=http://100.x.x.x:11434
```

---

## Option A — Home server (free, always-on)

Best if you have a spare machine (even CPU-only, 16GB+ RAM).

```bash
# On the server
brew install ollama          # or apt/curl install for Linux
export OLLAMA_HOST=0.0.0.0  # bind to all interfaces for Tailscale routing
ollama serve &
ollama pull llama3.1:8b

# Install Tailscale on both machines
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# Find the server's Tailscale IP
tailscale ip -4   # e.g. 100.x.x.x
```

Enter `http://100.x.x.x:11434` as the server URL in the app.

---

## Option B — Oracle Cloud Free Tier ARM (free, cloud, always-on)

Oracle's Always Free tier includes 4 Ampere ARM vCPUs + 24GB RAM — enough for llama3.1:8b.

1. Sign up at cloud.oracle.com → create an **Ampere A1** instance (Ubuntu 22.04)
2. SSH in and install Ollama:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   sudo systemctl enable ollama
   sudo systemctl start ollama
   ollama pull llama3.1:8b
   ```
3. Install Tailscale on the instance and your Mac
4. Use the Tailscale IP as the server URL

**Cost:** $0

---

## Option C — Hetzner CX22 (cheap, CPU-only)

- 2 vCPU, 4GB RAM — suitable for `llama3.2:3b` (smaller but faster)
- ~€4/month

```bash
ollama pull llama3.2:3b
```

Set model override to `llama3.2:3b` in the server settings.

---

## Option D — Vast.ai or RunPod GPU (burst use)

For occasional high-quality inference without a permanent server:

- Vast.ai RTX 3060 spot: ~$0.10–$0.20/hr
- RunPod community GPU: ~$0.10–$0.30/hr

Start a PyTorch or Ubuntu template, install Ollama, connect via Tailscale, use when needed.

---

## Security checklist

- **Never expose port 11434 to the public internet.** Ollama has no built-in auth.
- Always put Ollama behind a VPN tunnel (Tailscale is easiest).
- Confirm both devices are on the same Tailscale tailnet before saving the URL.
- The app probes the server on startup — if the VPN is down, it falls back automatically.

## Tailscale ACL (optional, recommended)

Restrict which Tailscale nodes can reach the server:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["your-mac-tag"],
      "dst": ["your-server-tag:11434"]
    }
  ]
}
```

## Privacy model

| Data | Stays on device? |
|------|-----------------|
| `local_only` attributes | Sent to private server (you own it), never to Anthropic/Groq |
| Raw question/answer text | Sent to private server after consent checkbox |
| `external_ok` attributes | May be sent to configured external providers |

Raw input still requires the consent checkbox in the Learn tab — it's a reminder that data leaves your Mac, even to a machine you own.
