# Passthrough example

This example category is for inputs where transformation risk is high enough that PromptPilot should leave the prompt unchanged.

Use passthrough when:

- The prompt is already precise.
- The SLM cannot confidently preserve all constraints.
- The input contains dense debugging context that may be important.
- A rewrite would risk changing intent or hiding a critical detail.

Passthrough is the correct safe fallback when uncertain.
