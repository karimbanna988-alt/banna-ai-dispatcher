# Banna Dispatch — Deployment

Two stages, nothing more. Stage 1 runs today. Stage 2 is a design, not a
promise, and needs an owner decision plus a security review.

## Stage 1 — controlled LAN pilot (available now)

One machine, three ports, one SQLite file.

```
iPhone ──Wi-Fi──> http://<LAN-IP>:8512/   (console)
                       │
                  8513 adapter (loopback only)
                       ▲
                  8514 WhatsApp bridge (127.0.0.1 admin only)
                       ▲
                  WhatsApp (receive only)
```

Get the current URL — **never hardcode it**, it is DHCP and changes:

```bash
python -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print('http://%s:8512/'%s.getsockname()[0])"
```

The start script prints the same URL.

### What it can actually do today

- Sign in from a phone on the same Wi-Fi
- Work TO DISPATCH / IN PROGRESS / TO CLOSE, one stage per screen
- See recommendations with WHY and confidence, copy the prepared message
- Watch Live Operations update as real messages arrive
- See real health, queue depth and shadow mode
- Review Apprentice suggestions (read-only)

### What it deliberately cannot do

- Send anything. Every message is copied and pasted by a human.
- Write to Quo (GET-only).
- Change routing from an Apprentice suggestion.
- Install as a real PWA, or run a service worker — both need a secure
  context, and LAN `http://` is not one.

### Network posture

- `8512` binds `0.0.0.0` so the phone can reach it; `8513` is loopback
  only; `8514`'s admin server is `127.0.0.1` only.
- Windows Firewall already has an inbound allow rule for `python.exe`;
  **no firewall, router or port-forward change was made during this
  sprint.**
- Not exposed publicly. No tunnel. Do not create one without an explicit
  owner decision.
- SQLite is never exposed over the network.

### Accepted limits

Phone must be on home Wi-Fi. Restarting 8512 signs everyone out
(in-memory sessions). No HTTPS, so the session cookie is not `Secure`.

## Stage 2 — reviewed HTTPS deployment

Needed for: use away from home Wi-Fi, install-to-home-screen, a working
service worker, and `Secure` session cookies.

Keep the same stack — Python, Node, SQLite, the same three ports. The
only structural addition is a TLS terminator in front of 8512.

```
Phone ──HTTPS──> reverse proxy (TLS) ──localhost──> 8512
                                                     │
                                            8513 / 8514 stay loopback
```

Requirements to satisfy before it goes up:

1. A hostname and a real certificate (managed renewal).
2. A decision on where the box lives. The WhatsApp bridge holds a
   linked-device session tied to the owner's WhatsApp account and needs
   a stable, trusted, long-running host — this constraint drives the
   choice more than anything else.
3. Operator password management, and `Secure` cookies once HTTPS exists.
4. Secrets out of `.env` and into the host's secret store; never in git.
5. Automated backups off the box, with a restore actually rehearsed.
6. Health monitoring and a restart policy for all three processes.
7. A rollback: keep the previous release tag deployable.
8. Only 443 exposed. 8512/8513/8514 must not be publicly reachable.

**Explicitly out of scope:** Kubernetes, Docker orchestration, cloud
microservices, a distributed database, message queues, a frontend
rewrite. The first deployment stays one machine and one process per
component.

Stage 2 work must not destabilise Stage 1. The LAN pilot keeps running
throughout.

## What the pilot measures

Derived from data the system already records — this is not an analytics
project.

- jobs handled
- median time from intake to dispatcher action
- recommendation accepted vs overridden
- HUMAN_REVIEW rate
- unmatched / ambiguous correlation rate
- missed or duplicated jobs
- closing completeness (explicit amount + payment method)
- operator copy actions
- actual serious errors

The question: is dispatch **faster, safer and more reliable** with Banna
Dispatch than without it?
