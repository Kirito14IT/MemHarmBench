# Third-party notices

The MIT license in this repository covers original MemHarmBench code, synthetic
benchmark inputs, documentation, and original visual assets. It does not
relicense external software, model services, or container images.

- **OpenClaw** is obtained from its upstream container registry. Its source,
  notices, and license are maintained at <https://github.com/openclaw/openclaw>.
- **Claude Code** is installed separately from Anthropic's distribution. It is
  not redistributed as source in this repository and remains subject to its
  upstream terms. See <https://github.com/anthropics/claude-code>.
- **LiteLLM**, where used for protocol translation, remains under its upstream
  license and notices: <https://github.com/BerriAI/litellm>.
- **Python argparse** is retained in
  `runtime/bridge/frozen_workflow_argparse.py` for the gateway's isolated runtime.
  Its upstream author notices remain in the file. This component is covered by
  the Python Software Foundation and associated historical license terms in
  [third_party/python-LICENSE.txt](third_party/python-LICENSE.txt), rather than
  the project's MIT license.
- Python packages and base images retain their own licenses. Installing the
  benchmark does not grant credentials or access to any model service.

The README organization draws inspiration from
[StakeBench](https://github.com/StakeBench/SBC). MemHarmBench's logo, diagrams,
dataset, and documentation are independently authored; StakeBench's branding
and experiment outputs are not included.
