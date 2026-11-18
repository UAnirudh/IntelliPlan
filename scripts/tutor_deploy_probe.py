"""Probe the production safety and generation chain with a fictional prompt."""
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    # SDK exceptions can contain request details. Emit only stage and error type.
    logging.disable(logging.CRITICAL)
    from school_pilot import enabled
    if enabled():
        print("TUTOR_DEPLOY_SMOKE skipped=isolated_pilot", flush=True)
        return 0
    import plani_safety
    from ai_provider import chat

    prompt = [{"role": "user", "content": "Explain the difference between the area and perimeter of a rectangle in two sentences."}]
    stage = "input_moderation"
    try:
        if plani_safety.classify(prompt) is not None:
            raise ValueError("Synthetic question withheld")
        print("TUTOR_DEPLOY_SMOKE input_moderation=ok", flush=True)
        stage = "generation"
        reply = chat(
            [{"role": "system", "content": "You are a concise school math tutor."}, *prompt],
            temperature=0, max_tokens=512, tier="fast",
        )
        if not reply or not reply.strip():
            raise ValueError("Provider returned an empty reply")
        print("TUTOR_DEPLOY_SMOKE generation=ok", flush=True)
        stage = "output_moderation"
        if plani_safety.classify(prompt, output=reply) is not None:
            raise ValueError("Synthetic reply withheld")
    except Exception as error:
        print(f"TUTOR_DEPLOY_SMOKE stage={stage} result=failed error={type(error).__name__}", flush=True)
        return 1
    print("TUTOR_DEPLOY_SMOKE output_moderation=ok result=passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
