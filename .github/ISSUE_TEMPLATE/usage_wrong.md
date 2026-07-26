---
name: Usage figures wrong or unavailable
about: A card says "unavailable", or the numbers do not match the provider
labels: provider
---

The usage endpoints are undocumented and change without notice, so this is the
most common kind of issue. The response shape is what makes it fixable.

**Provider**

Codex / Claude

**What the card shows**

**What the provider actually says**

(Whatever `/usage` shows in the official CLI, or the account page.)

**The response shape**

The important part. Key names and value *types* — please redact the values
themselves.

```json
{
  "five_hour": { "utilization": <number>, "resets_at": "<iso timestamp>" }
}
```

If you are comfortable doing so, `WATCHTOWER_LOG_LEVEL=DEBUG` plus the relevant
lines of `watchtower.log` helps too.

**Plan**

Which subscription tier, if you are happy to say. Limits differ per plan and
some responses only appear on some of them.
