# Private Server Setup

This guide covers running identity-engine inference on a remote Ollama instance you control, instead of relying on local hardware or cloud APIs.

## Why use a private server?

- Your Mac doesn't have enough RAM/GPU for a capable local model (Intel Mac 16GB = slow 3B model)
- You want full-size model inference without sending data to Anthropic/Groq
- Your private server is trusted hardware you own — `local_only` attributes are allowed to travel there over an encrypted tunnel

## Quick start

1. Set up an Ollama server (see options below)
2. In the app: **Settings → Providers → Private server → enter URL → Save**
3. Select the **Private server** profile
4. The backend pill in the header will show `private server`

Or via CLI:
```bash
make add-private-server-url URL=http://100.x.x.x:11434
```

---

## Model guide by server RAM

| Server RAM | Recommended model | Notes |
|------------|------------------|-------|
| 8 GB       | `llama3.2:3b`    | Fast; basic quality |
| 16 GB      | `llama3.1:8b`    | Good balance (default) |
| 24 GB      | `llama3.1:8b` or `mistral:7b` | Full quality |
| 32 GB+     | `llama3.1:70b` (Q4 quant) | Near-GPT-4 quality |

Pull your chosen model on the server before connecting:
```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text   # for retrieval/embeddings
```

---

## Option A — Home server (free, always-on)

Best if you have a spare machine (even CPU-only, 16 GB+ RAM).

```bash
# On the server
brew install ollama          # or apt/curl install for Linux
export OLLAMA_HOST=127.0.0.1  # bind loopback only; Tailscale handles routing
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

## Option B — Oracle Cloud Always Free ARM (recommended, $0/month)

Oracle's Always Free tier includes a **4 vCPU / 24 GB RAM Ampere A1** instance — enough for `llama3.1:8b` at good speed. This is the best free option for a permanent private server.

### 1. Create the instance

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com)
2. Go to **Compute → Instances → Create instance**
3. Choose:
   - **Shape:** Ampere A1 Flex — set 4 OCPUs and 24 GB RAM
   - **Image:** Ubuntu 22.04 (Minimal)
   - **Boot volume:** 50 GB (enough for 2–3 models)
4. Add your SSH public key (`~/.ssh/id_ed25519.pub`)
5. Under **Networking**, leave the default public subnet — you'll lock it down with UFW

### 2. Lock down the firewall

Oracle's VCN security list controls cloud-level ingress. Edit the default ingress rules:
- Keep: TCP 22 (SSH) from `0.0.0.0/0`
- Remove or restrict: everything else on port 11434

Then on the instance itself:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp         # SSH
# Allow Ollama only from Tailscale CGNAT range (added after Tailscale install)
sudo ufw allow from 100.64.0.0/10 to any port 11434
sudo ufw enable
```

### 3. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Configure Ollama to bind loopback only (Tailscale will route to it):
```bash
sudo systemctl edit ollama
```
Add:
```ini
[Service]
Environment="OLLAMA_HOST=127.0.0.1"
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

### 4. Install Tailscale and connect

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4   # note the 100.x.x.x address
```

Install Tailscale on your Mac too, then enter `http://100.x.x.x:11434` in the app.

**Cost:** $0

---

## Option C — Hetzner CX22 (cheap, CPU-only)

- 2 vCPU, 4 GB RAM — use `llama3.2:3b`
- ~€4/month

```bash
ollama pull llama3.2:3b
```

Set the model override to `llama3.2:3b` in the provider settings.

---

## Option D — Vast.ai or RunPod GPU (burst use)

For occasional high-quality inference without a permanent server:

- Vast.ai RTX 3060 spot: ~$0.10–$0.20/hr
- RunPod community GPU: ~$0.10–$0.30/hr

Start a PyTorch or Ubuntu template, install Ollama, connect via Tailscale, use when needed.

---

## Security hardening

### Critical rules

- **Never expose port 11434 to the public internet.** Ollama has no built-in authentication.
- Always put Ollama behind an encrypted tunnel (Tailscale is easiest; WireGuard if you want self-managed).
- Set `OLLAMA_HOST=127.0.0.1` — only listen on loopback; the tunnel does the rest.

### Tailscale ACL (recommended)

Restrict which Tailscale nodes can reach port 11434. In your [Tailscale admin console](https://login.tailscale.com/admin/acls):

```json
{
  "tagOwners": {
    "tag:inference-server": ["autogroup:admin"],
    "tag:identity-client":  ["autogroup:admin"]
  },
  "acls": [
    {
      "action": "accept",
      "src":    ["tag:identity-client"],
      "dst":    ["tag:inference-server:11434"]
    },
    {
      "action": "accept",
      "src":    ["autogroup:admin"],
      "dst":    ["tag:inference-server:22"]
    }
  ]
}
```

Tag your server node as `tag:inference-server` and your Mac as `tag:identity-client`.

### WireGuard alternative (self-managed)

If you prefer not to use Tailscale, set up a WireGuard peer-to-peer tunnel:

```bash
# Server
sudo apt install wireguard
wg genkey | sudo tee /etc/wireguard/server.key | wg pubkey | sudo tee /etc/wireguard/server.pub
```

`/etc/wireguard/wg0.conf` on server:
```ini
[Interface]
Address    = 10.0.0.1/24
PrivateKey = <server-private-key>
ListenPort = 51820

[Peer]
PublicKey  = <mac-public-key>
AllowedIPs = 10.0.0.2/32
```

Use `http://10.0.0.1:11434` as the server URL. Ensure `OLLAMA_HOST=10.0.0.1` to bind to the WireGuard interface.

---

## Verification

After configuring the server URL:

1. **Backend pill** in the app header shows `private server` (not `local` or `external`)
2. **Provider settings page** shows "validated" with a green checkmark
3. On the server, `ollama ps` confirms a model is loaded after your first query
4. The app health endpoint reports the active provider:
   ```bash
   curl http://localhost:8000/health | python3 -m json.tool
   # "provider": "private_server"
   ```

---

## Privacy model

| Data | Stays on device? |
|------|-----------------|
| `local_only` attributes | Sent to private server (you own it), **never** to Anthropic/Groq |
| `external_ok` attributes | May be sent to configured external providers |
| Raw question/answer text | Sent to private server after the consent checkbox |
| Artifacts | Sent to private server, **never** to external backends |

Raw input still requires the consent checkbox in the Learn tab — it's a reminder that data leaves your Mac, even to a machine you own.

---

## Troubleshooting

| Error | Likely cause | Fix |
|-------|-------------|-----|
| "Private server not reachable" | Tailscale disconnected, wrong IP, or `OLLAMA_HOST=127.0.0.1` without tunnel routing | Run `tailscale status` on both machines; check IP |
| "Model not found on private server" | Model not pulled | SSH in and run `ollama pull llama3.1:8b` |
| Slow responses | CPU-only inference | Expected; consider GPU instance or smaller model |
| Falls back to local/external | Server unreachable on startup | App auto-fallback is intentional; check server uptime |

The app probes the server at startup with a 2-second timeout. If it's unreachable, it silently falls back to local Ollama or external APIs — it never blocks inference.
