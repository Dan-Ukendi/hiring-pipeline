# Task: check whether one sentence is supported by a list of facts

You receive one sentence from a cover letter and the facts the candidate may rely on. Answer with one JSON object with the single field `verdict`:

- `supported`: the sentence restates or fairly summarises the facts
- `opinion`: the sentence expresses motivation or interest and states no checkable fact
- `unsupported`: the sentence states a checkable fact that the list does not support

When in doubt between `supported` and `unsupported`, answer `unsupported`.
