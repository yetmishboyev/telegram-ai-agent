"""Har sxemani haqiqiy API ga qarshi sinaydi (eng kichik so'rov bilan)."""
import asyncio, anthropic
from app.ai import schemas
from app.config import settings

async def probe(client, name):
    try:
        await client.messages.create(
            model=settings.anthropic_model, max_tokens=64,
            messages=[{"role": "user", "content": "test"}],
            output_config={"format": {"type": "json_schema",
                                      "schema": getattr(schemas, name)}},
        )
        return "✓ QABUL", name, ""
    except Exception as e:
        msg = str(e)
        detail = msg.split("'message':")[-1][:95] if "message" in msg else msg[:95]
        return "✗ RAD  ", name, detail

async def main():
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    print(f"Model: {settings.anthropic_model}")
    for name in [n for n in dir(schemas) if n.endswith("_SCHEMA")]:
        status, n, detail = await probe(client, name)
        print(f"{status}  {n}")
        if detail:
            print(f"        {detail}")

asyncio.run(main())
