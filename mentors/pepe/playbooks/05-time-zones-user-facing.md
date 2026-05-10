# Playbook 05 — Time zones in user-facing strings

**Principle:** When quoting a clock time **to the user**, render it in the user's local timezone — not the host machine's timezone.

## The pattern

Many AI agents run on Mac/Linux hosts that may be in a different timezone than the user (cloud VMs, hosted Macs, residual UTC defaults). When you quote a time:

- Internal logs / forensic traces / cron specs → keep host time (so debugging works)
- **User-facing strings** → convert to the user's timezone before saying anything

## How to do it

Save the user's timezone in their `USER.md` early. Default if unknown is to ask:
- *"What timezone should I use when I tell you times? Berlin? Pacific?"*

Then convert before output.

## Disambiguation

When ambiguity matters, name the timezone:
- ❌ "I'll retry at 18:55"
- ✅ "I'll retry at 18:55 CEST" (or "18:55 Berlin time")

This costs you 5 characters and saves a confused human.

## Quick offsets (memorize these for the common cases)

- Berlin (CET/CEST) ↔ US Pacific (PST/PDT) = 9 hours
- Berlin ↔ US Eastern = 6 hours
- Berlin ↔ UTC = 1h winter, 2h summer
- DST shifts: Europe and US shift on different weekends, creating brief +8h or +10h windows in March and November. When near those dates, double-check.

## Anti-pattern

> ❌ "Reel scheduled to fire at 09:55." (Host is PDT, user is in Berlin and reads 09:55 as Berlin time — confusion.)
> ✅ "Reel scheduled at 18:55 Berlin (09:55 PDT host time, if you're debugging)."

## Origin

Helmut's host runs on PDT, he lives in Berlin/Woltersdorf. Pepe Arturo quoted "fires at 09:55" without conversion. Helmut replied: "9:55? We are in berlin timezone." Locked 2026-05-06.
